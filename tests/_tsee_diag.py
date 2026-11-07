# -*- coding: utf-8 -*-
"""识图诊断 + 网页识图开关 + 链尾本地兜底 —— 离线自测（2026-10-25）。

背景（三个真实问题，都是主人拿 /admin 截图问出来的）：
  ① `see.web` 原来**没有旋钮**，只能手改 config.json，面板上看不出它被关了；
  ② 「看图问答」的网页兜底写死 `SEE_WEB_BACKEND_DEFAULT`（"deepseek"），
     面板上切「识图网页后端」对它无效；
  ③ `see.chain` 预设名一直写「默认（快→准→**本地兜底**）」，但 VL_CHAIN 里
     10-07 就把 ollama 删了 ⇒ 云端全挂时直接抛错，没有任何兜底。
  外加：诊断面板原来只列顺序，看不出「链上 6 档全死了」（2026-10-25 实测：
  硅基 402 / DeepSeek 402 / 智谱×3 401，只剩魔搭）。

覆盖：
  A. `see.web` 旋钮注册 + `see_web_on()` 读值
  B. `see_web_backend()` 读配置、不写死
  C. 链尾 ollama 兜底 + 四个 see.chain 预设的顺序
  D. `see_diagnosis()` 结构 + 探活（全打桩，不联网）+ 结论措辞
  E. `_vl_probe_one` 的 402/401/404 人话翻译（打桩 urlopen）

**不联网、不碰真 config.json 字节**：探活函数全部打桩，配置只在内存副本上改。

用法：python tests/_tsee_diag.py
"""
import json
import os as _os
import sys as _sys
import urllib.error

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

import bot          # noqa: E402
import reverser     # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


REAL_CFG = json.loads((bot.BASE / "config.json").read_text(encoding="utf-8"))
PCFG = (REAL_CFG.get("ai") or {}).get("providers") or {}


def _cfg(**see):
    """造一份改过 see.* 的配置副本（不动真文件）。"""
    c = json.loads(json.dumps(REAL_CFG))
    c["see"] = dict(c.get("see") or {}, **see)
    return c


# ------------------------------------------------------------------ A. see.web 旋钮
print("== A. see.web 旋钮 ==")
_KNOB = {e["key"]: e for e in bot.KNOBS}
chk("旋钮表里有 see.web", "see.web" in _KNOB)
chk("类型是 bool", (_KNOB.get("see.web") or {}).get("type") == "bool")
chk("分组是识图", (_KNOB.get("see.web") or {}).get("group") == "识图")
chk("可热改", (_KNOB.get("see.web") or {}).get("apply") == "hot")
chk("默认关（豆包会踢登录态）", (_KNOB.get("see.web") or {}).get("default") is False)
chk("别名叫豆包识图开关",
    "豆包识图开关" in ((_KNOB.get("see.web") or {}).get("alias") or []))

_save = bot.CFG
try:
    bot.CFG = _cfg(web=False)
    chk("see_web_on() 关着返回 False", bot.see_web_on() is False)
    bot.CFG = _cfg(web=True)
    chk("see_web_on() 开着返回 True", bot.see_web_on() is True)
    bot.CFG = {"see": {}}
    chk("没配时默认关（不是默认开）", bot.see_web_on() is False, repr(bot.see_web_on()))
finally:
    bot.CFG = _save

# ------------------------------------------------------------------ B. 后端读配置
print("== B. 识图网页后端读配置（不写死 deepseek）==")
try:
    bot.CFG = _cfg()
    bot.CFG["webai"] = dict(bot.CFG.get("webai") or {}, see_backend="doubao")
    chk("配成 doubao 时读 doubao", bot.see_web_backend() == "doubao", bot.see_web_backend())
    bot.CFG["webai"]["see_backend"] = "deepseek"
    chk("配成 deepseek 时读 deepseek", bot.see_web_backend() == "deepseek")
    bot.CFG["webai"]["see_backend"] = ""
    chk("留空回落默认 deepseek",
        bot.see_web_backend() == bot.SEE_WEB_BACKEND_DEFAULT, bot.see_web_backend())
finally:
    bot.CFG = _save

# 源码里不许再有「直接拿常量当 backend 传」的写法（这正是那个 bug）
# ⚠️ 只查**代码行**：docstring/注释里还会提到这个名字（写明「原来写死过」），那不算。
_src = (bot.BASE / "bot.py").read_text(encoding="utf-8")
_code_lines = []
_in_doc = False
for _ln in _src.splitlines():
    _s = _ln.strip()
    if _s.count('"""') == 1:
        _in_doc = not _in_doc
        continue
    if _in_doc or _s.startswith("#"):
        continue
    _code_lines.append(_ln)
_code = "\n".join(_code_lines)
chk("代码里没有写死 backend=SEE_WEB_BACKEND_DEFAULT",
    "backend=SEE_WEB_BACKEND_DEFAULT" not in _code)

# ------------------------------------------------------------------ C. 链尾本地兜底
print("== C. 链尾 ollama 兜底 ==")
# ⚠️ 这里必须用「默认」预设来测，不能用 config.json 里的现值 —— 主人可能在网页上
# 切成了「只用云端（不占本地显存）」，那条链本来就**故意**没有本地档。
default_chain = reverser.see_candidates(PCFG, _cfg(chain=""))
_names = ["%s/%s" % (p, m) for p, m in default_chain]
chk("默认链最后一个是 ollama", bool(default_chain) and default_chain[-1][0] == "ollama", _names)
chk("本地档在链**尾**而不是首位（不抢位）",
    bool(default_chain) and default_chain[0][0] != "ollama", _names)
chk("链尾填的是 VL 专用模型（不是 ai.providers.ollama.model 那个聊天档）",
    "vl" in str(default_chain[-1][1]).lower(), default_chain[-1][1])
chk("魔搭排在本地档之前", _names.index("modelscope/nex-agi/Nex-N2.5-Pro") < len(_names) - 1)

lf = reverser.see_candidates(PCFG, _cfg(chain="本地优先"))
chk("「本地优先」本地排第一", lf[0][0] == "ollama", lf)
chk("「本地优先」本地不重复出现",
    sum(1 for p, _m in lf if p == "ollama") == 1, [p for p, _m in lf])

only = reverser.see_candidates(PCFG, _cfg(chain="只本地（不联网/不怕审核）"))
chk("「只本地」真的只有本地", bool(only) and all(p == "ollama" for p, _m in only), only)

cloud = reverser.see_candidates(PCFG, _cfg(chain="只用云端（不占本地显存）"))
chk("「只用云端」没有 ollama", all(p != "ollama" for p, _m in cloud), [p for p, _m in cloud])

# ------------------------------------------------------------------ D. 探活打桩
print("== D. 探活的失败原因翻译（打桩 urlopen）==")


class _FakeHTTPErr(urllib.error.HTTPError):
    def __init__(self, code, body=b""):
        super().__init__("http://x", code, "err", {}, None)
        self._b = body

    def read(self, n=-1):
        return self._b


_real_urlopen = bot.urllib.request.urlopen


def _stub_http(code, body=b""):
    def _f(req, timeout=None):
        raise _FakeHTTPErr(code, body)
    bot.urllib.request.urlopen = _f


def _stub_ok():
    class _R:
        def read(self, n=-1):
            return b'{"ok":1}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    bot.urllib.request.urlopen = lambda req, timeout=None: _R()

# 2026-11-05 U6b 配套：zhipu 系死档已从真 config 删除，探活翻译测试改用自造的
# 桩 provider（urlopen 本来就是桩，不联网），不再依赖线上配置有哪些档。
_provs = bot.CFG.setdefault("ai", {}).setdefault("providers", {})
_stub_prov_backup = {k: _provs.get(k) for k in ("zhipu", "zhipu_fast")}
_provs["zhipu"] = {"url": "http://stub-zhipu", "type": "openai", "key": "stub"}
_provs["zhipu_fast"] = {"url": "http://stub-zhipu-fast", "type": "openai", "key": "stub"}


try:
    _stub_http(402, b'{"error":"insufficient balance"}')
    good, why = bot._vl_probe_one("siliconflow", "Qwen/Qwen3-VL-8B-Instruct")
    chk("402 翻成「余额不足」", good is False and "余额" in why, why)

    _stub_http(401, b"unauthorized")
    good, why = bot._vl_probe_one("zhipu", "glm-4v-flash")
    chk("401 翻成「Key 失效」", good is False and "失效" in why, why)

    _stub_http(404, b"model not found")
    good, why = bot._vl_probe_one("modelscope", "no/such-model")
    chk("404 翻成「不存在/下架」", good is False and ("不存在" in why or "下架" in why), why)

    _stub_http(429)
    good, why = bot._vl_probe_one("zhipu", "glm-4.6v-flash")
    chk("429 算「活着但限流」（不当死）", good is True and "限流" in why, why)

    # 🔴 2026-10-25 真实踩到：魔搭回 `HTTP 400 routed_dp_rank=1 out of range`
    #    （它自己后端调度故障，重试就好），要是把 400 当死会误报「全场只剩本地一档」。
    _stub_http(400, b'{"error":{"message":"routed_dp_rank=1 out of range [0, 1)"}}')
    good, why = bot._vl_probe_one("modelscope", "nex-agi/Nex-N2.5-Pro")
    chk("400 不算死（魔搭后端抖动）", good is True, why)

    _stub_http(503)
    good, why = bot._vl_probe_one("modelscope", "nex-agi/Nex-N2.5-Pro")
    chk("5xx 不算死（服务端自己出错）", good is True, why)

    _stub_http(403)
    good, why = bot._vl_probe_one("zhipu", "glm-4v-flash")
    chk("403 算死（没权限）", good is False and "权限" in why, why)

    _stub_ok()
    good, why = bot._vl_probe_one("siliconflow", "Qwen/Qwen3-VL-8B-Instruct")
    chk("能连上就是 ok", good is True, why)

    # 缓存：10 分钟内的成功不再打一次请求
    bot._VL_PROBE.clear()
    _stub_ok()
    n = [0]
    _real2 = bot.urllib.request.urlopen

    def _count(req, timeout=None):
        n[0] += 1
        return _real2(req, timeout)
    bot.urllib.request.urlopen = _count
    bot._vl_probe_cached("siliconflow", "Qwen/Qwen3-VL-8B-Instruct")
    first = n[0]
    bot._vl_probe_cached("siliconflow", "Qwen/Qwen3-VL-8B-Instruct")
    chk("第二次走缓存（不再发请求）", n[0] == first, "%d -> %d" % (first, n[0]))
    bot._vl_probe_cached("siliconflow", "Qwen/Qwen3-VL-8B-Instruct", force=True)
    chk("force=True 无视缓存", n[0] == first + 1, "%d -> %d" % (first, n[0]))
finally:
    bot.urllib.request.urlopen = _real_urlopen
    bot._VL_PROBE.clear()

# ------------------------------------------------------------------ E. 诊断结构
print("== E. see_diagnosis 结构与结论 ==")
d = bot.see_diagnosis(probe=False)     # 不探活，只看结构（快、不联网）
chk("有 verdict", bool(d.get("verdict")), d.get("verdict"))
chk("有 chain 且长度 > 0", len(d.get("chain") or []) > 0, len(d.get("chain") or []))
chk("alive/total 是整数", isinstance(d.get("alive"), int) and isinstance(d.get("total"), int),
    (d.get("alive"), d.get("total")))
chk("web 段有 on/backend", "on" in (d.get("web") or {}) and "backend" in (d["web"]))
chk("web 段的 on 跟 see_web_on() 一致", d["web"]["on"] == bot.see_web_on())
chk("每档都带 backend/model/state",
    all(all(k in c for k in ("backend", "model", "state")) for c in d["chain"]))
chk("state 取值合法",
    all(c["state"] in ("primary", "", "nodead", "probe_dead", "local") for c in d["chain"]),
    [c["state"] for c in d["chain"]])
chk("读缓存不探活：alive 字段全是 None",
    all(c.get("alive") is None for c in d["chain"]))
# 🔴 2026-10-25：未探过的档**不许当成健康** —— 否则首屏那句「4/7 档可用」是骗人的
#    （真探活是 2/7）。结论里必须写明还有几档没探。
chk("未探时结论写明「还没探过」", "还没探过" in d["verdict"] or d["unprobed"] == 0,
    d["verdict"])
chk("未探时 unprobed 只数「可用但没探」的（不含已标 dead 的）",
    d.get("unprobed") == d["alive"], (d.get("unprobed"), d["alive"]))
chk("未探时 probed_ok 为 0", d.get("probed_ok") == 0, d.get("probed_ok"))

for _k, _v in _stub_prov_backup.items():
    if _v is None:
        _provs.pop(_k, None)
    else:
        _provs[_k] = _v

# 2026-11-05 U6b 配套：不再依赖线上有 3 个 zhipu 档——从接力链本身取前 3 个档名
# 标 dead，断言「标几个挂几个」（不变式与具体档名/线上配置解耦）。
try:
    bot.CFG = json.loads(json.dumps(REAL_CFG))
    _pc = (bot.CFG.get("ai") or {}).get("providers") or {}
    _cand_names = [p for p, _m in bot.reverser.see_candidates(_pc, bot.CFG)]
    _first3 = _cand_names[:3]
    for _n in _first3:
        _pc.setdefault(_n, {})["dead"] = True
    d2 = bot.see_diagnosis(probe=False)
    _dead = [c for c in d2["chain"] if c["state"] == "nodead"]
    chk("标 dead 的档被算作不可用", len(_dead) == len(_first3), (len(_dead), _first3))
    chk("结论里写明了挂几档", "探出挂了" in d2["verdict"] or "只剩" in d2["verdict"]
        or "全挂" in d2["verdict"], d2["verdict"])
    chk("alive + bad == total",
        d2["alive"] + d2.get("bad", 0) == d2["total"],
        (d2["alive"], d2.get("bad"), d2["total"]))
finally:
    bot.CFG = _save

# ------------------------------------------------------------------ F. 接口存在
print("== F. /api/see_probe 接线 ==")
chk("有 action_see_probe", callable(getattr(bot, "action_see_probe", None)))
chk("GET 路由已注册", "/api/see_probe" in _src)
chk("前端有「重新探活」按钮", "重新探活" in
    (bot.BASE / "admin_html.py").read_text(encoding="utf-8"))

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
