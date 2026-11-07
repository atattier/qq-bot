# -*- coding: utf-8 -*-
"""给 custom 聊天链路接上 function calling（2026-10-29 深夜，主人拍板「接上吧」）。

主人的原话点破了架构死结：他的 Qoder CLI（agent 大脑）用的就是同一个 custom API——
**同一个模型在 agent 里能调工具，聊天链路没理由装不能**。差别从来不在模型，
在 bot 只给 agent 挂了 qqbot MCP，给 chat() 只发纯文本。

本补丁给 OpenAI 兼容链路实现标准的 tools/tool_calls 循环：
  - 工具执行**不绕 MCP**，直接走 bot 自己的 api()/send_group()（出站审计/熔断全在）；
  - 触发条件 = 主人派活（_task，词表见 _task_intent）——群友永远摸不到；
  - 刻意只放 QQ 动作（查群/查成员/看记录/发群/发私/等回复），**不给 Bash/文件** ——
    本机执行权是 agent 大脑的特权，聊天大脑再聪明也不给；
  - 优先级：_task ⇒ 带工具的聊天大脑（快、声线一致）⇒ 失败/不支持 ⇒ agent(qoder CLI) 兜底。
"""
import io
import os

B = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "bot.py")
s = io.open(B, encoding="utf-8").read()


def rep(old, new, tag):
    global s
    c = s.count(old)
    assert c == 1, "%s 锚点命中 %d" % (tag, c)
    s = s.replace(old, new)


# ================= 1) 定义区：插在 NON_API_BRAINS 之后 =================
OLD1 = 'NON_API_BRAINS = ("qoder", "agent", "dsh", "workbuddy", "zcode")  # agent=旧名，兼容老 config\n'
NEW1 = OLD1 + '''

# —— 带工具的聊天大脑（2026-10-29 深夜）——
class ToolsUnsupported(Exception):
    """网关/模型不支持 function calling（HTTP 4xx），调用方应回落 agent。"""


_QQ_TOOLS = [
    {"type": "function", "function": {
        "name": "qq_groups",
        "description": "拿到本 bot 加入的所有 QQ 群（群号+群名）。派活第一步先用它定位目标群。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "qq_members",
        "description": "列某个群的成员（QQ号+昵称+群名片）。要把「狐狐」这类昵称换成 QQ 号时用它。",
        "parameters": {"type": "object", "properties": {
            "group_id": {"type": "integer", "description": "群号"}},
            "required": ["group_id"]}}},
    {"type": "function", "function": {
        "name": "qq_history",
        "description": "看某群最近聊天记录（默认 20 条，上限 40），含发送者。探索前先看一眼群里在聊什么。",
        "parameters": {"type": "object", "properties": {
            "group_id": {"type": "integer"},
            "count": {"type": "integer", "description": "条数，默认 20"}},
            "required": ["group_id"]}}},
    {"type": "function", "function": {
        "name": "qq_send_group",
        "description": "往指定 QQ 群真发一条文本消息。发完想等对方回话就接着调 qq_wait_new。",
        "parameters": {"type": "object", "properties": {
            "group_id": {"type": "integer"},
            "message": {"type": "string", "description": "要发的消息文本"}},
            "required": ["group_id", "message"]}}},
    {"type": "function", "function": {
        "name": "qq_wait_new",
        "description": "等某个群出现新消息（最长 wait_sec 秒），返回新到的消息。发完消息用它等回复。",
        "parameters": {"type": "object", "properties": {
            "group_id": {"type": "integer"},
            "wait_sec": {"type": "integer", "description": "最长等几秒，默认 20，上限 45"}},
            "required": ["group_id"]}}},
]
_TOOL_MAX_ROUNDS = 8          # 工具循环上限，防死循环烧 token


def _tool_json(v):
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _ob11_text(seg):
    """OneBot 消息段（数组或字符串）→ 纯文本。"""
    if isinstance(seg, str):
        return seg
    if not isinstance(seg, list):
        return ""
    parts = []
    for sd in seg:
        if not isinstance(sd, dict):
            continue
        if sd.get("type") == "text":
            parts.append(str((sd.get("data") or {}).get("text", "")))
        elif sd.get("type") == "at":
            parts.append("@%s" % (sd.get("data") or {}).get("qq", ""))
        elif sd.get("type") == "image":
            parts.append("[图片]")
        elif sd.get("type") == "face":
            parts.append("[表情]")
    return "".join(parts)


def _group_history(gid, count=20):
    """拉群最近消息，转成精简列表；失败返回 []。"""
    try:
        r = api("get_group_msg_history", group_id=int(gid), count=int(count))
        raw = ((r.get("data") or {}).get("messages") or []) if isinstance(r, dict) else []
    except Exception:
        return []
    out = []
    for m in raw[-int(count):]:
        out.append({"time": m.get("time"), "user_id": m.get("user_id"),
                    "nickname": (m.get("sender") or {}).get("nickname"),
                    "text": _ob11_text(m.get("message")) or "[非文本]"})
    return out


def _exec_qq_tool(name, args):
    """执行一个 QQ 工具。发送走 send_group（出站审计/占位符过滤都在里面），查询走 api()。"""
    a = args if isinstance(args, dict) else {}
    if name == "qq_groups":
        r = api("get_group_list")
        gl = (r.get("data") or []) if isinstance(r, dict) else []
        return {"groups": [{"group_id": g.get("group_id"), "group_name": g.get("group_name")}
                           for g in gl][:50]}
    if name == "qq_members":
        r = api("get_group_member_list", group_id=int(a.get("group_id") or 0))
        ml = (r.get("data") or []) if isinstance(r, dict) else []
        return {"members": [{"user_id": m.get("user_id"),
                             "nickname": m.get("nickname"), "card": m.get("card")}
                            for m in ml][:200]}
    if name == "qq_history":
        cnt = max(1, min(int(a.get("count") or 20), 40))
        return {"messages": _group_history(a.get("group_id") or 0, cnt)}
    if name == "qq_send_group":
        gid = int(a.get("group_id") or 0)
        text = str(a.get("message") or "").strip()
        if not gid or not text:
            return {"ok": False, "error": "group_id 和 message 都必填"}
        ok = send_group(gid, text)
        return {"ok": bool(ok), "sent_to_group": gid}
    if name == "qq_wait_new":
        gid = int(a.get("group_id") or 0)
        wait = max(3, min(int(a.get("wait_sec") or 20), 45))
        base_list = _group_history(gid, 1)
        base_ts = 0.0
        if base_list:
            try:
                base_ts = float(base_list[-1].get("time") or 0)
            except (TypeError, ValueError):
                base_ts = 0.0
        deadline = time.time() + wait
        fresh = []
        while time.time() < deadline:
            time.sleep(2)
            fresh = [m for m in _group_history(gid, 20)
                     if float(m.get("time") or 0) > base_ts]
            if fresh:
                break
        return {"new_messages": fresh, "waited_sec": wait}
    return {"ok": False, "error": "未知工具 %s" % name}


def chat_once_tools(name, messages, tools=None, max_rounds=_TOOL_MAX_ROUNDS):
    """带 function calling 的 chat_once。返回 (最终文本, 是否动过工具)。

    只对 OpenAI 兼容链路有效；网关不支持 tools（HTTP 4xx）⇒ 抛 ToolsUnsupported，
    调用方回落 agent。每轮工具结果回填后继续，最多 max_rounds 轮。
    """
    p = provider_cfg(name)
    if not p:
        raise RuntimeError("config.json 的 ai.providers 里没有 %s" % name)
    if str(p.get("type") or "openai").lower() == "ollama":
        raise ToolsUnsupported("ollama 链路不挂 function tools")
    url = (p.get("url") or p.get("base_url") or "").rstrip("/")
    timeout = max(int(p.get("timeout") or 60), 120)
    headers = {"Authorization": "Bearer " + (p.get("key") or "")}
    cap = max(int(p.get("max_tokens") or 800), 1500)
    msgs = list(messages)
    used_tool = False
    for _ in range(max_rounds):
        payload = {"model": p.get("model", ""), "messages": msgs,
                   "temperature": p.get("temperature", 0.7), "max_tokens": cap,
                   "tools": (tools if tools is not None else _QQ_TOOLS),
                   "tool_choice": "auto"}
        payload.update(p.get("extra") or {})
        try:
            d = _post_json(url + "/chat/completions", payload, headers, timeout=timeout)
        except RuntimeError as e:
            m = re.search(r"HTTP (\\d+)", str(e))
            code = int(m.group(1)) if m else 0
            if code in (400, 404, 422):
                raise ToolsUnsupported(str(e))
            raise
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            return _strip_think(msg.get("content") or ""), used_tool
        used_tool = True
        msgs.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": calls})
        for tc in calls:
            fn = ((tc.get("function") or {}).get("name")) or ""
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                result = _exec_qq_tool(fn, args)
            except Exception as e:
                result = {"ok": False, "error": _short_err(str(e))}
            log.info("工具调用[%s] %s -> %s", name, fn, _tool_json(result)[:140])
            msgs.append({"role": "tool", "tool_call_id": tc.get("id") or "",
                         "content": _tool_json(result)[:4000]})
    return "", used_tool


def _task_chat_with_tools(gid, content, pb):
    """派活轮入口：给聊天大脑挂 QQ 工具。返回 (后端名, 回答)；没跑成回答为空串。"""
    name = str(pb or CFG.get("ai", {}).get("provider", "") or "").strip()
    p = provider_cfg(name)
    if (not p) or (name in NON_API_BRAINS) or (not str(p.get("url") or "").strip()):
        return "", ""
    import qbot_persona as _qp
    sys_text = (str(_qp.BASE_RULES or "").strip() +
                "\\n\\n【本次派活】你现在带着 QQ 工具上岗：qq_groups 定位群、qq_members 把昵称"
                "换成 QQ 号、qq_history 看群在聊什么、qq_send_group 真发消息、qq_wait_new 等回复。"
                "主人让你做什么就**真的用工具去做**，别用嘴说「这就去」；一步干不完就分多步"
                "（先看再发、发完等回复再汇报）。全部做完后，把结果当作给主人的回话讲出来。"
                "保持你平时的说话声线。")
    msgs = [{"role": "system", "content": sys_text}]
    for h in list(HISTORY.get(gid) or ())[-6:]:
        msgs.append({"role": h.get("role", "user"),
                     "content": str(h.get("content", ""))[:400]})
    msgs.append({"role": "user", "content": str(content or "")})
    try:
        ans, used = chat_once_tools(name, msgs)
    except ToolsUnsupported as e:
        log.info("网关不支持 tools（%s），交回 agent 兜底", e)
        return name, ""
    except Exception as e:
        log.warning("带工具聊天失败[%s]: %s", name, _short_err(str(e)))
        return name, ""
    log.info("带工具聊天[%s] 完成（用工具=%s）", name, used)
    return name, (ans or "").strip()
'''
rep(OLD1, NEW1, "定义区")

# ================= 2) 路由：保存 _pb 原值 =================
OLD2 = '''    _task = bool(_task_intent(content)) and is_master(uid) and _task_intent_enabled()
    if _pb and _task:
        log.info("私聊大脑让位：这条命中任务意图，转 agent 大脑干活")
        _pb = None'''
NEW2 = '''    _task = bool(_task_intent(content)) and is_master(uid) and _task_intent_enabled()
    _pb_saved = _pb
    if _pb and _task:
        log.info("私聊大脑让位：这条命中任务意图，转带工具的聊天大脑（agent 兜底）")
        _pb = None'''
rep(OLD2, NEW2, "路由保存pb")

# ================= 3) 工具聊天分支：插在 agent 主分支之前 =================
OLD3 = '''    if (not _pb) and (agent_selected() or _task) and agent_enabled() and _tools_ok and _owner_ok:
        # 2026-10-29：先把这一轮的图（本条 / 引用的）落到本地，把路径交给 agent。'''
NEW3 = '''    # 2026-10-29 深夜：任务意图优先「带工具的聊天大脑」—— 主人的 custom 模型在 agent
    # 里能调工具，聊天链路就没理由装不能（工具循环见 chat_once_tools）。跑不成再落
    # agent(qoder CLI)。工具只在主人派活时挂，群友永远摸不到；也不给 Bash/文件。
    if _task and _tools_ok and _owner_ok:
        _tname, _tans = _task_chat_with_tools(group_id, content, _pb_saved)
        if _tans:
            answer = _tans
            with _hist_lock:
                HISTORY[group_id].append({"role": "assistant", "content": answer})
            slang_learn_from_answer(content, answer)
            log.info("任务完成[%s] group=%s: %s", _tname, group_id, answer[:80])
            _memory_log_turn(group_id, who, uid, content, answer)
            send_bubbles(group_id, answer, want_image,
                         reply_to=(mid or 0) if not is_priv(group_id) else 0,
                         ctx_text=content)
            return
        log.info("带工具聊天没成（%s），转 agent 大脑兜底", _tname or "无可用API后端")

    if (not _pb) and (agent_selected() or _task) and agent_enabled() and _tools_ok and _owner_ok:
        # 2026-10-29：先把这一轮的图（本条 / 引用的）落到本地，把路径交给 agent。'''
rep(OLD3, NEW3, "工具聊天分支")

io.open(B, "w", encoding="utf-8", newline="").write(s)
print("聊天链路 function calling 补丁已写入")
