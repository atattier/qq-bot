# -*- coding: utf-8 -*-
"""M3 Tool Registry SAFE：覆盖 / 权限矩阵 / 越权 / ToolResult / MCP 生成 / 回退。

全离线：不发消息、不联网、不执行真实工具、不写生产 state。
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
os.environ.pop("QBOT_ARCH_TOOLS", None)
os.environ.pop("QBOT_ARCH_SHADOW", None)
os.environ.pop("QBOT_ARCH_CONTEXT", None)
sys.stdout.reconfigure(encoding="utf-8")

from qbot import qbot_tools as R     # noqa: E402
import bot                 # noqa: E402

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("  [OK] " if ok else "  [失败] ") + name + ("" if ok else " | %s" % (why,)))


def load_mcp():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "qq_mcp_server_t", Path(_ROOT) / "qq-mcp-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


member = R.Principal.chat(role="member")
admin = R.Principal.chat(role="admin", is_privileged=True)
owner = R.Principal.chat(role="owner", is_master=True, is_privileged=True)
stranger = R.Principal.chat(role="stranger")
bridge = R.Principal.system("bridge")

print("== 1 所有可调用能力都有 ToolSpec（总控 14 + MCP 20） ==")
reg = bot._tool_registry()
cov = reg.coverage()
chk("总控工具一个不落", cov["total"] == len(bot._QQ_TOOLS) + len(bot._BOT_TOOLS) == 14,
    cov["total"])
chk("覆盖报告无缺项", cov["clean"], cov["invalid"])
chk("每个都有名字/说明/参数 schema",
    all(s.name and s.description and isinstance(s.input_schema, dict) for s in reg.all()))
chk("每个都有 scope/副作用/幂等/超时",
    all(s.scope in R.SCOPES and s.side_effect in R.SIDE_EFFECTS
        and isinstance(s.idempotent, bool) and s.timeout_sec > 0 for s in reg.all()))
chk("有副作用的工具都标了不可逆/写",
    all(s.side_effect != "none" for s in reg.all() if s.name in
        ("qq_send_group", "remember", "send_meme", "draw_image", "gen_video")))
chk("别名 cloud_draw 解析回 draw_image",
    reg.get("cloud_draw") is reg.get("draw_image") and reg.get("cloud_draw").name == "draw_image")
chk("未知工具不解析", reg.get("no_such_tool") is None)

mcp = load_mcp()
mcov = mcp.REGISTRY.coverage()
chk("MCP 工具一个不落", mcov["total"] == len(mcp.TOOLS) == 20, mcov["total"])
chk("MCP 覆盖无缺项", mcov["clean"], mcov["invalid"])
chk("MCP 工具全部是桥接（system）面",
    all(s.scope == R.SYSTEM and s.surface == "mcp" for s in mcp.REGISTRY.all()))
chk("MCP 每个工具都有 HTTP 映射", all(s.http for s in mcp.REGISTRY.all()),
    [s.name for s in mcp.REGISTRY.all() if not s.http])
_get = {p for p, _ in bot._API_GET_ROUTES}
_post = {p for p, _ in bot._API_POST_ROUTES}
_miss = [(s.name, m, p) for s in mcp.REGISTRY.all() for m, p in s.http
         if p not in (_get if m == "GET" else _post)]
chk("HTTP 兼容层路由全部存在", not _miss, _miss)

print("== 2 权限矩阵：public / admin / owner / system 四档 ==")


def _spec(name, scope):
    return R.ToolSpec(name=name, description="矩阵探针 " + scope, scope=scope,
                      input_schema={"type": "object", "properties": {}})


mreg = R.ToolRegistry([_spec("t_public", R.PUBLIC), _spec("t_admin", R.ADMIN),
                       _spec("t_owner", R.OWNER), _spec("t_system", R.SYSTEM)])
EXPECT = {
    "stranger": {"public": True, "admin": False, "owner": False, "system": False},
    "member":   {"public": True, "admin": False, "owner": False, "system": False},
    "admin":    {"public": True, "admin": True,  "owner": False, "system": False},
    "owner":    {"public": True, "admin": True,  "owner": True,  "system": False},
    "bridge":   {"public": True, "admin": True,  "owner": True,  "system": True},
}
for pname, p in (("stranger", stranger), ("member", member), ("admin", admin),
                 ("owner", owner), ("bridge", bridge)):
    got = {s: mreg.check(p, "t_" + s).allowed for s in R.SCOPES}
    chk("矩阵 %-8s rank=%d" % (pname, p.rank), got == EXPECT[pname], got)
chk("四个 scope 都有被覆盖", set(EXPECT["owner"]) == set(R.SCOPES))
chk("聊天身份永远拿不到 system", not any(
    p.allows(R.SYSTEM) for p in (stranger, member, admin, owner)))
chk("主人也受 system 档约束（不能冒充桥接）", not owner.allows(R.SYSTEM))

chk("群友工具面只有 8 个公开能力", len(reg.visible(member)) == 8,
    [s.name for s in reg.visible(member)])
chk("privileged（管理员/主人）拿全 14 个", len(reg.visible(admin)) == 14 and
    len(reg.visible(owner)) == 14)
chk("群友摘不到 qq_* / remember",
    not ({"qq_groups", "qq_members", "qq_history", "qq_send_group", "qq_wait_new",
          "remember"} & {s.name for s in reg.visible(member)}))
chk("没点名不许画：allow_draw=False 只剩 5 个",
    len(reg.visible(member, allow_draw=False)) == 5,
    [s.name for s in reg.visible(member, allow_draw=False)])
chk("allow_draw 只摘画图/视频三件",
    {s.name for s in reg.visible(member, allow_draw=False)} ==
    {"reverse_prompt", "web_search", "recall_memory", "send_meme", "bot_status"})
chk("契约工具面 == 现有工具面（群友）",
    [s.name for s in reg.visible(member)] ==
    [t["function"]["name"] for t in bot._omni_tools(False)])
chk("契约工具面 == 现有工具面（privileged）",
    [s.name for s in reg.visible(admin)] ==
    [t["function"]["name"] for t in bot._omni_tools(True)])
chk("契约工具面 == 现有工具面（allow_draw=False）",
    [s.name for s in reg.visible(member, allow_draw=False)] ==
    [t["function"]["name"] for t in bot._omni_tools(False, allow_draw=False)])
_lone = R.ToolSpec(name="t_group", description="d", scope=R.PUBLIC, group_only=True)
_privonly = R.ToolSpec(name="t_priv", description="d", scope=R.PUBLIC, private_only=True)
_lreg = R.ToolRegistry([_lone, _privonly])
chk("群专属工具私聊不给",
    not _lreg.check(R.Principal.chat(is_group=False), "t_group").allowed)
chk("私聊专属工具群里不给",
    not _lreg.check(R.Principal.chat(is_group=True), "t_priv").allowed)
chk("桥接不受会话类型限制",
    _lreg.check(bridge, "t_group").allowed and _lreg.check(bridge, "t_priv").allowed)

print("== 3 越权调用为零：影子口径 + 真实工具循环拦截 ==")
_all_names = [t["function"]["name"] for t in bot._omni_tools(True)]
chk("privileged 面与契约完全一致（无漂移）",
    reg.shadow_offer(admin, "omni", _all_names)["clean"],
    reg.shadow_offer(admin, "omni", _all_names))
chk("群友面与契约完全一致（无漂移）",
    reg.shadow_offer(member, "omni",
                     [t["function"]["name"] for t in bot._omni_tools(False)])["clean"])
_over = reg.shadow_offer(member, "omni", _all_names)
chk("群友被硬塞全量工具时能被抓出 6 处越界",
    set(_over["denied_but_offered"]) ==
    {"qq_groups", "qq_members", "qq_history", "qq_send_group", "qq_wait_new",
     "remember"}, _over["denied_but_offered"])
chk("越界报告不过审", not _over["clean"])
_dr = reg.shadow_call(member, "draw_image", True, allow_draw=False)
chk("资源闸也纳入契约（未点名画图=越权）", _dr["denied_but_called"], _dr)
_ok_call = reg.shadow_call(member, "draw_image", True, allow_draw=True)
chk("合规调用不误报", _ok_call["clean"], _ok_call)

# 端到端：模型硬写一个没提供的工具，真实工具循环必须拒绝执行
_sent = []
_orig_post, _orig_cfg, _orig_exec = bot._post_json, bot.provider_cfg, bot._exec_qq_tool
_round = {"n": 0, "payloads": []}
_face = bot._omni_tools(False, allow_draw=False)


def _fake_post(url, payload, headers, timeout=60):
    _round["n"] += 1
    _round["payloads"].append(payload)
    if _round["n"] == 1:
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "qq_send_group",
                                      "arguments": json.dumps({"group_id": 1,
                                                               "message": "不应该发出去"})}},
            {"id": "c2", "function": {"name": "draw_image",
                                      "arguments": json.dumps({"what": "猫"})}},
        ]}}]}
    return {"choices": [{"message": {"content": "好"}}]}


def _boom_exec(name, args):
    _sent.append(name)
    raise AssertionError("不该执行 %s" % name)


_orig_call = bot.qbot_tools.record_shadow
_recs = []
bot.provider_cfg = lambda n: {"type": "openai", "url": "http://fake", "key": "",
                              "model": "m"}
bot._post_json = _fake_post
bot._exec_qq_tool = _boom_exec
bot.qbot_tools.record_shadow = lambda kind, payload: _recs.append((kind, dict(payload)))
os.environ["QBOT_ARCH_TOOLS"] = "shadow"
try:
    _ans, _used = bot.chat_once_tools(
        "fake", [{"role": "user", "content": "hi"}], tools=_face,
        max_calls=6, principal=member, allow_draw=False)
finally:
    bot._post_json, bot.provider_cfg, bot._exec_qq_tool = _orig_post, _orig_cfg, _orig_exec
    bot.qbot_tools.record_shadow = _orig_call
    os.environ.pop("QBOT_ARCH_TOOLS", None)
chk("群友这一轮真的调了工具（模型层）", _used)
chk("两个越权工具一个都没执行", _sent == [], _sent)
_back = json.dumps(_round["payloads"][-1]["messages"], ensure_ascii=False)
chk("面拒绝如实回填给模型（两条都在）",
    _back.count("不在本轮工具面") == 2, _back[-300:])
_calls = [p for k, p in _recs if k == "call"]
chk("影子记下两次调用且都未执行",
    len(_calls) == 2 and all(not c["executed"] for c in _calls), _calls)
chk("影子判定：越权成功 = 0", not any(c.get("unauthorized_ok") for c in _calls))
chk("影子没记任何正文/参数", all("不应该发出去" not in json.dumps(c, ensure_ascii=False)
                          and "猫" not in json.dumps(c, ensure_ascii=False)
                          for c in _calls))

print("== 4 ToolResult：超时 / 幂等 / 非法参数 / 失败 ==")
rt = R.ToolRuntime()
espec = R.ToolSpec(name="echo", description="d", idempotent=True, timeout_sec=1,
                   input_schema={"type": "object", "required": ["x"],
                                 "properties": {"x": {"type": "integer"}}})
_n = {"c": 0}


def _echo():
    _n["c"] += 1
    return {"ok": True, "v": 1}


r1 = rt.execute(espec, {"x": 1}, _echo)
chk("成功返回 ok/status=ok", r1.ok and r1.status == "ok" and r1.data["v"] == 1, r1.to_dict())
r2 = rt.execute(espec, {"x": 1}, _echo)
chk("幂等调用不重复执行", _n["c"] == 1 and r2.replayed, (_n["c"], r2.replayed))
chk("ToolResult 可 JSON 序列化",
    isinstance(json.dumps(r1.to_dict(), ensure_ascii=False), str))
chk("ToolResult 带 schema_version", r1.schema_version == R.SCHEMA_VERSION)
_inv = rt.execute(espec, {}, _echo)
chk("缺必填参数 → invalid 且不执行",
    _inv.status == "invalid" and not _inv.ok and _n["c"] == 1, _inv.to_dict())
_bad = rt.execute(espec, {"x": "a"}, _echo)
chk("类型不对 → invalid", _bad.status == "invalid" and "integer" in _bad.error, _bad.error)
_nego = R.ToolSpec(name="nego", description="d", idempotent=False)
_n2 = {"c": 0}


def _e2():
    _n2["c"] += 1
    return {"ok": True}


rt.execute(_nego, {}, _e2)
rt.execute(_nego, {}, _e2)
chk("非幂等工具不复用结果", _n2["c"] == 2, _n2["c"])


def _raise():
    raise RuntimeError("后端炸了")


_er = rt.execute(espec, {"x": 2}, _raise)
chk("执行异常 → error", _er.status == "error" and "后端炸了" in _er.error, _er.to_dict())
_tmo = R.ToolSpec(name="slow", description="d", idempotent=False, timeout_sec=0.15)
_t0 = time.time()
_tr = rt.execute(_tmo, {}, lambda: (time.sleep(0.8), {"ok": True})[1])
chk("超时 → timeout 且按预算返回",
    _tr.status == "timeout" and (time.time() - _t0) < 1.0,
    (_tr.to_dict(), round(time.time() - _t0, 2)))
chk("只有 ok/error/denied/timeout/invalid/unsupported 六种状态",
    R.RESULT_STATUSES == {"ok", "error", "denied", "timeout", "invalid", "unsupported"})
try:
    R.ToolResult(tool="x", ok=False, status="weird")
    _reject = False
except ValueError:
    _reject = True
chk("未知状态被拒绝", _reject)
_deny = R.ToolResult.fail("t", "denied", "no")
chk("denied 状态可用", _deny.status == "denied" and not _deny.ok)
chk("legacy 字典 → ToolResult",
    R.ToolResult.from_legacy("qq_send_group", {"ok": False, "error": "x"}).status == "error")
chk("legacy 成功字典 → ok",
    R.ToolResult.from_legacy("qq_groups", {"groups": []}).ok)

print("== 5 MCP 工具由 Registry 生成且参数兼容 ==")
os.environ.pop("QQBOT_ARCH_TOOLS", None)
chk("默认 legacy 仍吐原表", mcp.list_tools() == mcp.TOOLS)
os.environ["QQBOT_ARCH_TOOLS"] = "registry"
_gen = mcp.list_tools()
chk("registry 模式由 Registry 生成", len(_gen) == 20)
chk("逐个工具 name/description/inputSchema 完全等价", _gen == mcp.TOOLS)
chk("参数 schema 原样保留（不是重写的空壳）",
    all(g["inputSchema"] == o["inputSchema"] for g, o in zip(_gen, mcp.TOOLS)))
chk("工具顺序不变", [g["name"] for g in _gen] == [o["name"] for o in mcp.TOOLS])
os.environ.pop("QQBOT_ARCH_TOOLS", None)


def _ast_fn(src, fn):
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name == fn:
            return ast.dump(n)
    return ""


try:
    _head = subprocess.run(["git", "show", "HEAD:qq-mcp-server.py"],
                           capture_output=True, text=True, encoding="utf-8",
                           cwd=_ROOT, timeout=20).stdout
except Exception:  # noqa: BLE001
    _head = ""
_cur = Path(_ROOT, "qq-mcp-server.py").read_text(encoding="utf-8")
if _head:
    chk("旧 dispatch_tool 逐 AST 未改（仍可回退）",
        _ast_fn(_head, "dispatch_tool") == _ast_fn(_cur, "dispatch_tool"))
chk("dispatch_tool 仍只有一个 tools/call 入口",
    _cur.count("def dispatch_tool(") == 1 and "dispatch_tool(p.get(\"name\")" in _cur)

print("== 6 影子审计落盘 + 一键回退 ==")
_tmp = Path(tempfile.mkdtemp(prefix="m3_tools_"))
R.bind_shadow_dir(_tmp / "tools")
bot.CFG.setdefault("arch", {})["tools"] = "legacy"
chk("默认 legacy（不开影子）", bot._arch_tools_mode() == "legacy")
bot._tool_shadow_offer(member, bot._omni_tools(False))
bot._tool_shadow_call(member, "draw_image", bot._omni_tools(False), True, {"ok": True})
chk("legacy 时一行审计都不写", not R.SHADOW_FILE.exists(), R.SHADOW_FILE)

bot.CFG["arch"]["tools"] = "shadow"
chk("arch.tools=shadow 启用影子", bot._arch_tools_mode() == "shadow")
bot._tool_shadow_offer(member, bot._omni_tools(False))
bot._tool_shadow_call(member, "draw_image", bot._omni_tools(False), True, {"ok": True})
bot._tool_shadow_call(member, "qq_send_group", bot._omni_tools(False), True,
                      {"ok": True, "sent_to_group": 1})
_lines = [json.loads(x) for x in R.SHADOW_FILE.read_text(encoding="utf-8").splitlines()
          if x.strip()]
chk("写出 offer + call 审计", len(_lines) == 3, len(_lines))
chk("offer 审计判定工具面无漂移", _lines[0]["kind"] == "offer" and _lines[0]["clean"],
    _lines[0])
chk("合规调用不报越权", _lines[1]["kind"] == "call" and
    not _lines[1]["unauthorized_ok"], _lines[1])
chk("越权成功会被明确标出来", _lines[2]["unauthorized_ok"] is True, _lines[2])
chk("审计不含消息正文/参数", "不应该发出去" not in R.SHADOW_FILE.read_text(encoding="utf-8"))
chk("审计带 schema_version", all(x.get("schema_version") == R.SCHEMA_VERSION
                            for x in _lines))
chk("审计文件落在临时目录", str(R.SHADOW_FILE).startswith(tempfile.gettempdir()))

os.environ["QBOT_ARCH_TOOLS"] = "legacy"
chk("env 可临时退回 legacy（优先于 config）",
    bot._arch_tools_mode() == "legacy")
os.environ["QBOT_ARCH_TOOLS"] = "shadow"
chk("env 也可临时打开 shadow", bot._arch_tools_mode() == "shadow")
os.environ.pop("QBOT_ARCH_TOOLS", None)
os.environ["QBOT_ARCH_TOOLS"] = "nonsense"
chk("非法值一律按 legacy（不会误开）", bot._arch_tools_mode() == "legacy")
os.environ.pop("QBOT_ARCH_TOOLS", None)

bot.CFG["arch"]["tools"] = "legacy"
_before = len(_lines)
bot._tool_shadow_offer(member, bot._omni_tools(True))
bot._tool_shadow_call(member, "qq_send_group", bot._omni_tools(True), True,
                      {"ok": True})
_after = len([x for x in R.SHADOW_FILE.read_text(encoding="utf-8").splitlines() if x.strip()])
chk("回退 legacy 后不再新增审计", _after == _before, (_before, _after))
chk("回退后旧工具面原样不动",
    [t["function"]["name"] for t in bot._omni_tools(True)] == _all_names and
    [t["function"]["name"] for t in bot._omni_tools(False)] ==
    [t["function"]["name"] for t in bot._omni_tools(False)])

print()
if bad:
    print("失败 %d 项" % bad)
    sys.exit(1)
print("全部通过")
