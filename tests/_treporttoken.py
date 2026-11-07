# -*- coding: utf-8 -*-
"""R4：NapCat → Bot 上报 token 认证。

锁住四件事：
  1. `napcat_report_token` 与 `config.token`（Bot → NapCat API token）职责分离；
  2. 配置上报 token 后，Authorization 严格校验，UA/X-Signature 不再能绕过；
  3. 未配置上报 token 时保持旧兼容行为，并输出一次不含 token 的告警；
  4. 多上报端：字符串或数组，任一 token 匹配即可；现有 NapCat 配置存在时逐个验。

全离线：不启动服务、不联网、不发群；不打印任何 token 值。
"""
import importlib.util
import inspect
import json
import logging
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

FAIL = []


def chk(label, cond, extra=""):
    """cond 为结果；extra 是 bool 时当期望值比较，否则当失败附加信息。"""
    if isinstance(extra, bool):
        ok = (cond == extra)
        detail = "got=%r want=%r" % (cond, extra)
    else:
        ok = bool(cond)
        detail = extra
    print(("  [OK] " if ok else "  [失败] ") + label + ("" if ok else " | " + str(detail)))
    if not ok:
        FAIL.append(label)


class _H(dict):
    """假 headers（大小写不敏感，跟 http.server 一样）。"""

    def items(self):
        return super().items()


def _auth(tok):
    return _H({"Authorization": "Bearer " + tok})


_orig_api_token = bot.CFG.get("token")
_orig_report_token = bot.CFG.get("napcat_report_token")
_orig_warned = bot._NAPCAT_REPORT_TOKEN_WARNED
_orig_self_id = bot.SELF_ID

API_TOKEN = "API_TOKEN_R4"
REPORT_A = "REPORT_TOKEN_A"
REPORT_B = "REPORT_TOKEN_B"
bot.CFG["token"] = API_TOKEN
bot.SELF_ID = 123450004

print("=== R4：NapCat 上报 token 认证 ===")

# ---------- 1. 单上报端严格校验 ----------
bot.CFG["napcat_report_token"] = REPORT_A
chk("正确上报 token 通过",
    bot._looks_like_napcat(_auth(REPORT_A), {}), True)
chk("错误上报 token 拒绝",
    bot._looks_like_napcat(_auth(REPORT_B), {}), False)
chk("config.token 不能当上报 token（职责分离）",
    bot._looks_like_napcat(_auth(API_TOKEN), {}), False)
chk("裸上报 token（无 Bearer）通过",
    bot._looks_like_napcat(_H({"Authorization": REPORT_A}), {}), True)

# ---------- 2. 多上报端 ----------
bot.CFG["napcat_report_token"] = [REPORT_A, REPORT_B]
chk("数组：上报端 A 通过", bot._looks_like_napcat(_auth(REPORT_A), {}), True)
chk("数组：上报端 B 通过", bot._looks_like_napcat(_auth(REPORT_B), {}), True)
chk("数组：错误 token 拒绝", bot._looks_like_napcat(_auth("REPORT_C"), {}), False)

# ---------- 3. 缺失 / 畸形 Authorization ----------
bot.CFG["napcat_report_token"] = REPORT_A
chk("缺失 Authorization 拒绝（即使 self_id 正确）",
    bot._looks_like_napcat(_H(), {"self_id": 123450004, "post_type": "message"}), False)
chk("空 Authorization 拒绝",
    bot._looks_like_napcat(_H({"Authorization": ""}), {}), False)
chk("只有 Bearer 没有值拒绝",
    bot._looks_like_napcat(_H({"Authorization": "Bearer"}), {}), False)
chk("Bearer 后只有空白拒绝",
    bot._looks_like_napcat(_H({"Authorization": "Bearer   "}), {}), False)
chk("Basic 认证拒绝",
    bot._looks_like_napcat(_H({"Authorization": "Basic " + REPORT_A}), {}), False)
chk("token 后带多余内容拒绝",
    bot._looks_like_napcat(_H({"Authorization": "Bearer " + REPORT_A + " extra"}), {}), False)

# ---------- 4. 配置后 UA / X-Signature 不能绕过 ----------
chk("配置上报 token 后 UA: NapCat 不再放行",
    bot._looks_like_napcat(_H({"User-Agent": "NapCat/1.0"}), {}), False)
chk("配置上报 token 后任意 X-Signature 不再放行",
    bot._looks_like_napcat(_H({"X-Signature": "abc"}), {}), False)

# ---------- 5. 未配置：保持旧兼容行为 + 告警 ----------
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = _Capture()
_old_level = bot.log.level
bot.log.addHandler(cap)
bot.log.setLevel(logging.DEBUG)
try:
    bot.CFG["napcat_report_token"] = []
    bot._NAPCAT_REPORT_TOKEN_WARNED = False
    chk("未配置时 UA: NapCat 仍放行（旧兼容行为）",
        bot._looks_like_napcat(_H({"User-Agent": "NapCat/1.0"}), {}), True)
    chk("未配置时旧 config.token 判据仍可用",
        bot._looks_like_napcat(_auth(API_TOKEN), {}), True)
finally:
    bot.log.removeHandler(cap)
    bot.log.setLevel(_old_level)

chk("未配置时产生明确告警",
    any("napcat_report_token" in m and "未配置" in m for m in cap.lines), cap.lines[-3:])
chk("告警不含任何 token 值",
    not any((API_TOKEN in m) or (REPORT_A in m) or (REPORT_B in m) for m in cap.lines), cap.lines[-3:])

# ---------- 6. Bot → NapCat API 仍使用 config.token ----------
_captured = {}


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"status": "ok", "retcode": 0}'


def _fake_urlopen(req, timeout=30):
    _captured["auth"] = req.get_header("Authorization")
    return _Resp()


_orig_urlopen = bot.urllib.request.urlopen
bot.urllib.request.urlopen = _fake_urlopen
try:
    bot.CFG["napcat_report_token"] = REPORT_A
    bot.api("get_status")
finally:
    bot.urllib.request.urlopen = _orig_urlopen

chk("Bot→NapCat API 仍用 config.token",
    _captured.get("auth") == "Bearer " + API_TOKEN, _captured)

# ---------- 7. 认证失败不进入消息业务链 ----------
_src = inspect.getsource(bot.Handler.do_POST)
_gate = _src.index("if not _looks_like_napcat")
_dispatch = _src.rindex("threading.Thread(target=dispatch")
chk("普通入口先鉴权、后 dispatch", _gate < _dispatch, (_gate, _dispatch))
chk("鉴权失败在 dispatch 前返回 403",
    "403" in _src[_gate:_dispatch] and "return" in _src[_gate:_dispatch], "")

# ---------- 8. 现有 NapCat 上报端逐个匹配（存在时） ----------
_cfg_dir = os.path.join(_ROOT, "docker-napcat", "config")
_sources = []
try:
    for _f in sorted(os.listdir(_cfg_dir)):
        if not (_f.startswith("onebot11_") and _f.endswith(".json")):
            continue
        try:
            with open(os.path.join(_cfg_dir, _f), encoding="utf-8") as _fh:
                _d = json.load(_fh)
        except Exception:
            continue
        for _c in ((_d.get("network") or {}).get("httpClients") or []):
            _t = str((_c or {}).get("token") or "").strip()
            if _t:
                _sources.append((_f, _t))
except Exception:
    _sources = []

if _sources:
    _tokens = []
    for _, _t in _sources:
        if _t not in _tokens:
            _tokens.append(_t)
    bot.CFG["napcat_report_token"] = _tokens[0] if len(_tokens) == 1 else _tokens
    print("  实际配置：%d 个上报端 / %d 个唯一 token" % (len(_sources), len(_tokens)))
    for _f, _t in _sources:
        chk("实际上报端可认证：%s" % _f, bot._looks_like_napcat(_auth(_t), {}), True)
else:
    print("  [SKIP] 未找到 docker-napcat/config/onebot11_*.json，实际端到端匹配留待现场验证")

# ---------- 9. wire 同步：只读 httpClients.token，多上报端写数组 ----------
_wire_path = os.path.join(_ROOT, "docker-napcat", "wire.py")
_spec = importlib.util.spec_from_file_location("docker_wire_r4", _wire_path)
_wire = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wire)

with tempfile.TemporaryDirectory() as _td:
    _ncfg = os.path.join(_td, "napcat")
    os.makedirs(_ncfg)
    _cases = (("onebot11_a.json", "RPT_A", "SRV_A"),
              ("onebot11_b.json", "RPT_B", "SRV_B"))
    for _name, _rpt, _srv in _cases:
        with open(os.path.join(_ncfg, _name), "w", encoding="utf-8") as _fh:
            json.dump({"network": {
                "httpClients": [{"enable": True, "token": _rpt}],
                "httpServers": [{"enable": True, "token": _srv}],
            }}, _fh)
    _botcfg = os.path.join(_td, "config.json")
    with open(_botcfg, "w", encoding="utf-8") as _fh:
        json.dump({"token": "API_ONLY"}, _fh)

    _old_ncfg = _wire.U.NAPCAT_CFG_DIR
    _old_botcfg = _wire.U.BOT_CFG
    _old_backup = _wire.U.backup
    _wire.U.NAPCAT_CFG_DIR = _ncfg
    _wire.U.BOT_CFG = _botcfg
    _wire.U.backup = lambda *a, **k: ""
    try:
        _srcs = _wire._report_token_sources()
        _toks = [t for _, t in _srcs]
        chk("wire 收集到两个上报端", len(_srcs) == 2, len(_srcs))
        chk("wire 只收集 httpClients.token", sorted(_toks) == ["RPT_A", "RPT_B"], "")
        chk("wire 不收集 httpServers.token",
            "SRV_A" not in _toks and "SRV_B" not in _toks, "")
        _wire.wire_report_token(True)
        with open(_botcfg, encoding="utf-8") as _fh:
            _d = json.load(_fh)
        chk("wire --check 不写 bot 配置", "napcat_report_token" not in _d, "")
        _wire.wire_report_token(False)
        with open(_botcfg, encoding="utf-8") as _fh:
            _d = json.load(_fh)
        chk("wire 写入两个唯一 token 的数组",
            _d.get("napcat_report_token") == ["RPT_A", "RPT_B"], "")
    finally:
        _wire.U.NAPCAT_CFG_DIR = _old_ncfg
        _wire.U.BOT_CFG = _old_botcfg
        _wire.U.backup = _old_backup

# ---------- 恢复测试态 ----------
if _orig_report_token is None:
    bot.CFG.pop("napcat_report_token", None)
else:
    bot.CFG["napcat_report_token"] = _orig_report_token
bot.CFG["token"] = _orig_api_token
bot._NAPCAT_REPORT_TOKEN_WARNED = _orig_warned
bot.SELF_ID = _orig_self_id

print()
print("=== 汇总 ===")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
