# -*- coding: utf-8 -*-
"""QQ 动作 MCP 服务器（stdio，纯标准库）。

给无头 agent（任何 MCP 客户端）用：agent 自己看群消息、自己决定发哪句、
自己提黑话候选。所有真正的 QQ 操作都转给 bot.py 的本地 API（127.0.0.1:8088/api/*），
白名单、出站审计、频控都在 bot.py 那边硬执行——这个文件里没有密钥，也没有本机写操作。

配置从环境变量读：
  QQBOT_API   默认 http://127.0.0.1:8088
  QQBOT_TOKEN bot.py 生成的 bridge_token（同目录 state/bridge-token.json，或直接传 token）
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Windows 控制台/管道默认 GBK，MCP 走 stdio JSON 必须是 UTF-8，否则中文工具描述会变乱码
for _s in (sys.stdin, sys.stdout):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

API = (os.environ.get("QQBOT_API") or "http://127.0.0.1:8088").rstrip("/")
TOKEN = os.environ.get("QQBOT_TOKEN") or ""
HERE = os.path.dirname(os.path.abspath(__file__))


def load_token():
    global TOKEN
    if TOKEN:
        return TOKEN
    for f in (os.path.join(HERE, "state", "bridge-token.json"),
              os.path.join(HERE, "state", "bridge_token.json")):
        try:
            if os.path.exists(f):
                TOKEN = json.load(open(f, encoding="utf-8")).get("token", "")
                if TOKEN:
                    return TOKEN
        except Exception:
            pass
    try:  # 兜底：直接从 config.json 读
        TOKEN = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8")).get(
            "bridge_token", "")
    except Exception:
        pass
    return TOKEN


def call(method, params=None, timeout=45):
    load_token()
    url = API + method
    data = None
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, data=data, headers={"X-Bridge-Token": TOKEN or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "HTTP %s（token 不对或 bot 没起）" % e.code}
    except Exception as e:
        return {"ok": False, "error": "连不上 bot 的本地接口：%s" % str(e)[:160]}


def post(method, payload, timeout=45):
    load_token()
    req = urllib.request.Request(API + method, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Bridge-Token": TOKEN or "",
                                          # bot 侧靠它区分「谁改的配置」（审计日志用）
                                          "X-QQ-Source": "agent"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


TOOLS = [
    {"name": "qq_status",
     "description": "看机器人现在的状态：自己的QQ号/昵称、被允许的群、当前人格、图库、已确认与待确认的黑话。发言前先调一次。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "qq_groups",
     "description": "列出机器人所在的QQ群（群号、群名、人数）。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "qq_members",
     "description": "列某个群的成员（QQ号/昵称/身份）。只能查白名单里的群。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer", "description": "群号"}},
                     "required": ["group_id"]}},
    {"name": "qq_history",
     "description": "读群里最近的聊天记录（带说话人、时间差、以及可用于引用回复的 mid）。传 after 只看比该 id 更新的消息，用来做未读轮询。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "limit": {"type": "integer", "description": "条数，默认30，最多200"},
                                    "after": {"type": "integer", "description": "只返回 id 大于此值的消息"}},
                     "required": []}},
    {"name": "qq_wait_for_messages",
     "description": "阻塞等待群里来新消息（长轮询）：最多挂 timeout_sec 秒，期间一有新消息立刻返回；超时返回空列表。值班循环专用——处理完一轮就调它继续等，不要用 qq_history 高频轮询刷屏。返回的 last_id 记下来当下一轮的 after。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "after": {"type": "integer",
                                              "description": "上次看到的最大消息 id，只等比它新的"},
                                    "timeout_sec": {"type": "integer",
                                                    "description": "最多等多少秒，10~55，默认 40"}},
                     "required": []}},
    {"name": "qq_send",
     "description": "往群里或私聊发一句话（可选带图库里的图、可选引用某条 mid）。只有白名单群能发；内容含本机路径或密钥会被拦截。"
                    "**发私聊**：group_id 填负的目标 QQ 号（如 -123450006），且必须带 group_hint=你俩共同所在的群号 —— "
                    "非好友只能走 QQ 的「群临时会话」，不给 group_hint 会发不出去。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer",
                                                 "description": "群号（正数）或私聊目标 QQ 号（负数，如 -123450006）"},
                                    "text": {"type": "string", "description": "要发的正文，口语、简短"},
                                    "image": {"type": "string",
                                              "description": "图库里的图片名（qq_status 的 images 字段），留空=纯文字"},
                                    "reply_to_mid": {"type": "integer",
                                                     "description": "要引用的消息 mid（来自 qq_history），可不填"},
                                    "group_hint": {"type": "integer",
                                                   "description": "群号。**发私聊给非好友时必填**，用来走群临时会话；发群消息不用填"}},
                     "required": ["group_id", "text"]}},
    {"name": "qq_generate",
     "description": "让本机 ComfyUI 现画一张图。默认 Anima 动漫档（1024x1536，主人自己的工作流，约两三分钟，具体以返回的 eta_sec 为准，别答应「马上」）。异步：返回 ok=true 即已排队（status=queued），图画好会自动发到群里，所以别重复提交、别轮询、别在群里播报进度。被挡时 ok=false 且 error 给出原因。要快就传 style=fast（约一分钟内的 Krea2 轻量档）。SD1.5 已停用，别提 anime/real。描述里的角色名/历史人物/作品名/梗，链路会自己识别并联网查外观设定再画（多花几秒，查过就缓存）——你别自己脑补外观写进 prompt，把用户那句原样传就行；纯景物或用户已说清外观时传 lookup=false 省时间。想发图库里现成的图请用 qq_send 的 image 字段，别用这个。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "prompt": {"type": "string",
                                               "description": "画什么，中文一句话说清主体+环境+光线，例如：穿汉服的白虎鲸少女，海边，夕阳逆光"},
                                    "style": {"type": "string",
                                              "description": "anima(默认档，1024x1536) / krea2(画质档，8步+放大，较慢) / fast(轻量档，约半分钟，不放大)，留空=默认档"},
                                    "landscape": {"type": "boolean",
                                                  "description": "true=横构图（宽屏画面）"},
                                    "lookup": {"type": "boolean",
                                               "description": "要不要先联网查证描述里的角色/专有名词设定，默认 true；纯景物或不需要查证时传 false 省几秒"},
                                    "reply_to_mid": {"type": "integer",
                                                    "description": "画好后引用哪条消息（一般是提问者的 mid），可不填"}},
                     "required": ["group_id", "prompt"]}},
    {"name": "qq_slang_list",
     "description": "看黑话库（confirmed 的词条会被注入到聊天人格里；pending 的等管理员确认）。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "qq_slang_propose",
     "description": "把看不懂的网络新词提为黑话候选（只能是 pending，转正必须由管理员在群里发「黑话 确认 词 意思」）。",
     "inputSchema": {"type": "object",
                     "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
                     "required": ["term"]}},
    {"name": "qq_meme_lookup",
     "description": "查一个网络梗到底什么意思、怎么用、褒贬、还算不算新鲜。群友说了看不懂的词先查这个，别硬猜也别装懂；返回「库里没有」就说明它真不在库（现有条目见 qq_meme_catalog），这时可以说你不确定，或联网查清后再用 qq_meme_teach 记下来。",
     "inputSchema": {"type": "object",
                     "properties": {"term": {"type": "string",
                                             "description": "要查的词，如 yyds、破防了、松弛感"}},
                     "required": ["term"]}},
    {"name": "qq_meme_catalog",
     "description": "看梗库目录（有哪些梗、大概分几类、总共多少条）。想主动玩梗但不确定库里有啥时先扫一眼。",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer",
                                              "description": "最多列几条，默认 60"}},
                     "required": []}},
    {"name": "qq_meme_teach",
     "description": "把一个已经查清楚的新梗收进本地梗库，以后全机器人都能用。必须真查明白再用，别把自己瞎猜的解释写进去。梗名重复会覆盖更新。",
     "inputSchema": {"type": "object",
                     "properties": {"term": {"type": "string", "description": "梗名"},
                                    "meaning": {"type": "string", "description": "一句话说清什么意思"},
                                    "usage": {"type": "string",
                                              "description": "怎么用，给个例句最好"},
                                    "aliases": {"type": "string",
                                                "description": "别名，逗号分隔，可不填"}},
                     "required": ["term", "meaning"]}},
    {"name": "qq_meme_pick",
     "description": "按关键词/情绪挑几张梗图，只返回候选（编号+画面描述），不发出去。想配图但不确定挑到啥时用；挑好再把编号交给 qq_meme_send。",
     "inputSchema": {"type": "object",
                     "properties": {"keyword": {"type": "string",
                                                "description": "情绪或场景，如 生气、摆烂、夸人、无语"},
                                    "character": {"type": "string",
                                                  "description": "想要哪个角色当主角，可不填"},
                                    "limit": {"type": "integer", "description": "几条，默认 8"},
                                    "group_id": {"type": "integer",
                                                 "description": "发往哪个群（用来避开刚发过的）"}},
                     "required": []}},
    {"name": "qq_meme_send",
     "description": "直接把一张梗图发到群里（会自动挑图、下载、验真，图床不稳时会换一张或报失败）。适合「接一句梗图比说话更有味道」的场合：**一条回复最多一张**，别每句都发，也别在文字里预告「我给你找个图」。有每小时/每天额度，被挡会返回原因，遇到被挡就正常说话，别反复试。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "keyword": {"type": "string",
                                                "description": "情绪或场景，如 生气、笑死、无语、夸人"},
                                    "character": {"type": "string",
                                                  "description": "指定角色，可不填"},
                                    "pick": {"type": "integer",
                                             "description": "qq_meme_pick 里的编号，从 1 开始；不填就自己挑"},
                                    "caption": {"type": "string",
                                                "description": "配一句话，可不填（图本身有字就别配了）"}},
                     "required": ["group_id"]}},
    {"name": "qq_fibi_pick",
     "description": "从本机的「菲比表情包」图库里挑几张候选（编号+文件名），不发出去。想发自己的表情包但不确定挑到啥时用；挑好把编号交给 qq_fibi_send。图全在本机，不联网。",
     "inputSchema": {"type": "object",
                     "properties": {"keyword": {"type": "string",
                                                "description": "语气或场景，如 无语、开心、干饭、摆烂、生气"},
                                    "limit": {"type": "integer", "description": "几条，默认 8"},
                                    "group_id": {"type": "integer",
                                                 "description": "发往哪个群（用来避开刚发过的）"}},
                     "required": []}},
    {"name": "qq_fibi_send",
     "description": "把一张「菲比」表情包（本机图库，动图为主）发到当前会话 —— 这是用你自己的脸做个表情，比贴别人的梗图更自然。适合接一句情绪、或者群友点名要图。**一条回复最多一张**，别每句都发，别在文字里预告「我给你找个图」，也别描述图里画了什么。有每小时/每天额度，被挡会返回原因，遇到被挡就正常说话，别反复试。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "keyword": {"type": "string",
                                                "description": "语气或场景，如 无语、开心、干饭、摆烂、生气"},
                                    "pick": {"type": "integer",
                                             "description": "qq_fibi_pick 里的编号，从 1 开始；不填就自己挑"},
                                    "caption": {"type": "string",
                                                "description": "配一句话，可不填（图本身有字就别配了）"}},
                     "required": ["group_id"]}},
    {"name": "qq_petpet",
     "description": "把某个人的头像做成「捏脸」动图发到当前会话：摸摸头 / 揉一揉 / 咬一口 / 敲一敲 / 揍一拳 / 捶 / 撞 / 滚 / 扭 / 扔出去 / 猫猫揉 / 支持你。适合对方撒娇、装可怜、讨夸、被调侃时来一下，比打字更有味道。**一次只做一个**，别连着刷，也别在文字里预告「我给你做一个」。群友直接说「摸摸头 @某人」时系统会自己出图，不用你调。有冷却和每小时额度，被挡就正常说话，别反复试。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "qq": {"type": "string",
                                           "description": "做谁的头像（对方 QQ 号）；不填就是发话人自己"},
                                    "template": {"type": "string",
                                                 "description": "动作：petpet摸摸头 / pat拍拍 / rub揉一揉 / bite咬一口 / knock敲一敲 / punch揍一拳 / pound捶 / thump撞 / roll滚 / twist扭 / throw_gif扔出去 / capoo_rub猫猫揉 / support支持你。不填用默认"}},
                     "required": ["group_id"]}},
    {"name": "qq_recall",
     "description": "查你长期记住的群友画像（跨会话保存，重启不丢，也不受聊天记录窗口限制）。传 query 按关键词或人名检索（例如「考研」「阿伟」，一个词就够），不传就列最近记的。不确定某人的细节时先查一下，**查不到就别瞎编**。返回结果带 id，删东西时要用。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "query": {"type": "string",
                                              "description": "关键词或人名，留空=列最近记的"},
                                    "limit": {"type": "integer", "description": "最多几条，默认20"}},
                     "required": ["group_id"]}},
    {"name": "qq_remember",
     "description": "长期记住某个群友的一件事（跨会话保存，重启不丢）。只记稳定的个人信息和癖好，一句话一条，例如「在准备考研」「养了只布偶猫叫团子」「不吃香菜」。别记流水账（他今天吃了饭这种没价值）、别记敏感隐私。重复内容会自动去重。action=forget 时改为删除：传 fact_id 删一条，或传 qq 删掉此人的全部记忆（本人要求忘记时必须配合删）。",
     "inputSchema": {"type": "object",
                     "properties": {"group_id": {"type": "integer"},
                                    "content": {"type": "string", "description": "要记住的一句话"},
                                    "qq": {"type": "string",
                                           "description": "这个人的QQ号，有就填，方便按人检索"},
                                    "name": {"type": "string", "description": "这个人的昵称"},
                                    "action": {"type": "string",
                                               "description": "add(默认)=记住 / forget=忘记"},
                                    "fact_id": {"type": "integer",
                                                "description": "forget 时指定删哪一条（用 qq_recall 查）"}},
                     "required": ["group_id"]}},
    {"name": "qq_config",
     "description": "看或改机器人的运行配置（画图档、思考强度、人格、联网、活跃度、冷却等）。不传参数=列出所有可调项与当前值；只传 key=查这一项；key+value=改一项；传 profile=一键切预设档。key 用中文名，如「画图档」「思考强度」「人格」「联网」「活跃」。密钥类只回报「已配置/未配置」，拿不到明文。改动立即生效并写进 config.json——改前先想清楚，别替群友乱动主人的配置。",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string",
                                            "description": "项目名（画图档/思考强度/人格/联网/活跃/冷却…），可空"},
                                    "value": {"type": "string",
                                              "description": "新值；只查时不传"},
                                    "profile": {"type": "string",
                                                "description": "预设档名：日常/创作/省电/安静"}},
                     "required": []}},
]

# ---------------- M3 Tool Registry（ADR-001 §7.3）----------------
# 三套工具面收敛成一套契约。这里只改 **tools/list 的来源**：
#   默认 legacy（照旧直接吐上面的 TOOLS，零变化）；
#   QQBOT_ARCH_TOOLS=registry 时由 qbot_tools.ToolRegistry 生成（字段与原表逐项等价）。
# 🔴 dispatch_tool 一行不动 —— 真正执行/权限/白名单仍在 bot.py 的 HTTP 层。
try:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import qbot_tools as _arch_tools
    REGISTRY = _arch_tools.build_mcp_registry(TOOLS)
except Exception:  # noqa: BLE001
    _arch_tools = None
    REGISTRY = None


def arch_tools_mode():
    v = str(os.environ.get("QQBOT_ARCH_TOOLS") or "legacy").strip().lower()
    return v if v in ("legacy", "registry") else "legacy"


def list_tools():
    if arch_tools_mode() == "registry" and REGISTRY is not None:
        return REGISTRY.mcp_tools()
    return TOOLS


def dispatch_tool(name, args):
    args = args or {}
    if name == "qq_status":
        return call("/api/status")
    if name == "qq_groups":
        return call("/api/groups")
    if name == "qq_members":
        return call("/api/members", {"group": args.get("group_id")})
    if name == "qq_history":
        return call("/api/history", {"group": args.get("group_id"),
                                     "limit": min(int(args.get("limit") or 30), 200),
                                     "after": int(args.get("after") or 0)})
    if name == "qq_wait_for_messages":
        t = min(max(int(args.get("timeout_sec") or 40), 10), 55)
        return call("/api/wait_messages", {"group": args.get("group_id"),
                                           "after": int(args.get("after") or 0),
                                           "timeout": t},
                    timeout=t + 10)
    if name == "qq_send":
        return post("/api/send", {"group": args.get("group_id"), "text": args.get("text") or "",
                                  "image": args.get("image") or "",
                                  "reply_to": args.get("reply_to_mid") or 0,
                                  # 2026-10-27：私聊非好友要走群临时会话，必须把群号递进去。
                                  # 之前这里没这个字段，agent 想私聊只能干瞪眼（主人 22:47 那次）。
                                  "group_hint": args.get("group_hint") or 0})
    if name == "qq_generate":
        return post("/api/generate", {"group": args.get("group_id"), "prompt": args.get("prompt") or "",
                                      "style": args.get("style") or "",
                                      "landscape": bool(args.get("landscape")),
                                      "lookup": args.get("lookup", True),
                                      "reply_to": args.get("reply_to_mid") or 0})
    if name == "qq_slang_list":
        return call("/api/slang")
    if name == "qq_slang_propose":
        return post("/api/slang", {"term": args.get("term"), "meaning": args.get("meaning") or ""})
    if name == "qq_meme_lookup":
        # 2026-10-26：先查梗百科（本机 6668 条），没收录再回落「网络流行梗」那个小库
        # （小库 38 条底稿 + 自学习，留着是为了兼容以前教过的词，别丢）。
        r = post("/api/skill", {"skill": "memewiki", "tool": "lookup",
                                "args": {"term": args.get("term") or ""}})
        if "没有收录" in str(r) or "没有这个技能工具" in str(r):
            r2 = post("/api/skill", {"skill": "trendy-memes", "tool": "lookup",
                                     "args": {"term": args.get("term") or ""}})
            if "梗库里没有" not in str(r2):
                return r2
        return r
    if name == "qq_meme_catalog":
        return post("/api/skill", {"skill": "trendy-memes", "tool": "catalog",
                                   "args": {"limit": int(args.get("limit") or 60)}})
    if name == "qq_meme_teach":
        al = args.get("aliases") or []
        if isinstance(al, str):
            al = [x.strip() for x in al.replace("，", ",").split(",") if x.strip()]
        return post("/api/skill", {"skill": "trendy-memes", "tool": "teach",
                                   "args": {"term": args.get("term") or "",
                                            "meaning": args.get("meaning") or "",
                                            "usage": args.get("usage") or "",
                                            "aliases": al}})
    if name == "qq_meme_pick":
        return post("/api/skill", {"skill": "whale-memes", "tool": "list",
                                   "args": {"keyword": args.get("keyword") or "",
                                            "character": args.get("character") or "",
                                            "limit": int(args.get("limit") or 8),
                                            "cid": args.get("group_id") or 0}})
    if name == "qq_meme_send":
        return post("/api/skill", {"skill": "whale-memes", "tool": "send",
                                   "args": {"cid": args.get("group_id"),
                                            "keyword": args.get("keyword") or "",
                                            "character": args.get("character") or "",
                                            "pick": int(args.get("pick") or 0),
                                            "count": 1,
                                            "caption": args.get("caption") or ""}},
                    timeout=90)
    if name == "qq_fibi_pick":
        return post("/api/skill", {"skill": "fibi-memes", "tool": "list",
                                   "args": {"keyword": args.get("keyword") or "",
                                            "limit": int(args.get("limit") or 8),
                                            "cid": args.get("group_id") or 0}})
    if name == "qq_fibi_send":
        return post("/api/skill", {"skill": "fibi-memes", "tool": "send",
                                   "args": {"cid": args.get("group_id"),
                                            "keyword": args.get("keyword") or "",
                                            "pick": int(args.get("pick") or 0),
                                            "count": 1,
                                            "caption": args.get("caption") or ""}},
                    timeout=60)
    if name == "qq_petpet":
        return post("/api/skill", {"skill": "petpet", "tool": "send",
                                   "args": {"cid": args.get("group_id"),
                                            "qq": str(args.get("qq") or ""),
                                            "template": args.get("template") or "",
                                            "sender_qq": str(args.get("sender_qq") or "")}},
                    timeout=120)
    if name == "qq_recall":
        return call("/api/memory", {"group": args.get("group_id"),
                                    "query": args.get("query") or "",
                                    "limit": min(int(args.get("limit") or 20), 60)})
    if name == "qq_remember":
        if str(args.get("action") or "add") == "forget":
            return post("/api/memory", {"group": args.get("group_id"), "action": "forget",
                                        "fact_id": args.get("fact_id"), "qq": args.get("qq")})
        return post("/api/memory", {"group": args.get("group_id"),
                                    "content": args.get("content") or "",
                                    "qq": args.get("qq") or "",
                                    "name": args.get("name") or ""})
    if name == "qq_config":
        if args.get("profile"):
            return post("/api/config", {"profile": args.get("profile")})
        if not args.get("key"):
            return call("/api/config")
        body = {"key": args.get("key")}
        if args.get("value") is not None:
            body["value"] = args.get("value")
        return post("/api/config", body)
    return {"ok": False, "error": "没有这个工具 " + name}


SERVER = {"name": "qqbot", "version": "1.0"}
PROTOCOL = "2024-11-05"


def reply(obj_id, result):
    out({"jsonrpc": "2.0", "id": obj_id, "result": result})


def error(obj_id, code, msg):
    out({"jsonrpc": "2.0", "id": obj_id, "error": {"code": code, "message": msg}})


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            reply(rid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                        "serverInfo": SERVER})
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "ping":
            reply(rid, {})
        elif method == "tools/list":
            reply(rid, {"tools": list_tools()})
        elif method == "tools/call":
            p = req.get("params") or {}
            try:
                res = dispatch_tool(p.get("name"), p.get("arguments"))
            except Exception as e:
                res = {"ok": False, "error": str(e)[:200]}
            reply(rid, {"content": [{"type": "text",
                                     "text": json.dumps(res, ensure_ascii=False)[:6000]}],
                        "isError": not (res or {}).get("ok", False)})
        elif rid is not None:
            error(rid, -32601, "unsupported method: %s" % method)


if __name__ == "__main__":
    main()
