# -*- coding: utf-8 -*-
"""
hy3 后台巡逻大脑：跑到当天 06:00，长轮询群 123450001。
由 hy3（WorkBuddy 会话）编写大脑逻辑，推理走 config 里配好的 LLM provider
（patrol.json 的 provider 若为 "hy3" 则自动回退到 zhipu 等真实 provider）。

发言策略（用户 2026-09-14 凌晨要求，比原纪律更活跃）：
- 被 @小鲸(123450002) 必回；
- 消息提到小鲸/鲸/AI/机器人/bot（和我有关）-> 自然接话；
- 和我好像没关系的话题 -> 也可以插一两句（短反应或有信息量的接话），但别每条都插、别喧宾夺主；
- 群里长时间安静 -> 主动抛一个轮播话题暖场；
- 硬纪律：每条 <=30 字、绝不自称 AI/助手/模型、不重复别人刚说的、不写 markdown/CQ 标签、
  不主动说要画图（画图指令 bot 自己接）、拿不准就轻反应或沉默。
分级限频：有关/@ 的回复最小间隔 25s；无关的插话最小间隔 90s；暖场话题间隔 >=9 分钟。
每条发言都写回 state/wb_poll.json 游标；brain != workbuddy 自动收工。
依赖：bot.py 在 127.0.0.1:8088 暴露 /api/*（与 _wbqq.py 同协议）。无第三方依赖。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
API = "http://127.0.0.1:8088"
GROUP = 123450001
SELF_QQ = 123450002
CURSOR_FILE = os.path.join(HERE, "state", "wb_poll.json")
LOG_FILE = os.path.join(HERE, "state", "patrol_hy3.log")
CFG_PATH = os.path.join(HERE, "config.json")
PATROL_CFG = os.path.join(HERE, "state", "patrol.json")

# 当天 06:00 收工；若当前已过 06:00 则顺延到明天
_now = datetime.now()
STOP_AT = _now.replace(hour=6, minute=0, second=0, microsecond=0)
if STOP_AT <= _now:
    STOP_AT = STOP_AT.replace(day=_now.day + 1)

# 被 @小鲸 时 LLM 仍可能判不回，给个兜底短句
AT_FALLBACK = [
    "咋啦 叫本鲸干啥", "嗯？本鲸在呢", "又咋了呀",
    "说吧 本鲸听着", "哼 叫本鲸干嘛", "本鲸没睡 讲",
]
# LLM 挂了时，无关的插话兜底池（短反应，安全不踩雷）
CHIME_FALLBACK = [
    "哈哈哈", "笑死", "本鲸也觉得", "然后呢", "真的假的",
    "蹲一个", "本鲸路过", "有道理呀", "说得对", "草 笑不活了",
]
# 主动暖场话题（傲娇虎鲸口吻，<=30字，不自称AI）
TOPICS = [
    "有没有人还醒着 陪本鲸唠唠",
    "你们熬夜都在干啥呀 好奇",
    "半夜偷偷起来喝水的 是不是你",
    "本鲸饿了 你们宵夜吃的啥",
    "突然想问 你们都几点睡啊",
    "有没有失眠的 举手让本鲸看看",
    "深夜放毒时间到 谁还嘴硬",
    "本鲸刚刷到个沙雕视频笑死",
    "你们手机里最爱循环哪首歌",
    "困但舍不得睡的是不是就本鲸",
    "谁来给本鲸讲个冷笑话",
    "今天有啥开心事没 说说嘛",
    "本鲸宣布 现在是最佳熬夜时段",
    "你们打游戏还是刷视频呢",
    "突然馋奶茶了 谁懂",
    "有没有人也在赶作业的 抱抱",
    "本鲸觉得深夜最适合发呆",
    "你们睡前都听啥助眠啊",
    "谁还不是个夜猫子呢 哼",
    "本鲸刚想到个离谱的梦 你们呢",
    "半夜灵感最多 谁有同感",
    "你们最晚熬到几点过 报个数",
    "本鲸说 熬夜伤身但快乐 你们说呢",
]
# 提到小鲸/AI/机器人的关键词（判定"和我有关"）
RELATED_KW = ["小鲸", "鲸鲸", "鲸", "ai", "AI", "机器人", "bot", "Bot", "BOT",
              "助手", "模型", "智障", "人工智障", "人工智能"]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- token / config ----------------
def token():
    for f in ("state/bridge-token.json", "state/bridge_token.json"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            try:
                t = json.load(open(p, encoding="utf-8")).get("token", "")
                if t:
                    return t
            except Exception:
                pass
    try:
        return json.load(open(CFG_PATH, encoding="utf-8")).get("bridge_token", "")
    except Exception:
        return ""


TOK = token()


def get(url, params=None, timeout=60):
    u = API + url
    if params:
        u += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(u, headers={"X-Bridge-Token": TOK or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def post(url, payload, timeout=60):
    req = urllib.request.Request(
        API + url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Bridge-Token": TOK or "",
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
        return {"ok": False, "error": str(e)[:200]}


def get_cursor():
    try:
        n = json.load(open(CURSOR_FILE, encoding="utf-8")).get("last_mid", 0)
        if n:
            return int(n)
    except Exception:
        pass
    try:
        h = get("/api/history", {"group": GROUP, "limit": 1, "after": 0})
        ms = h.get("messages") or []
        if ms:
            return int(ms[-1]["id"])
    except Exception:
        pass
    return 0


def set_cursor(n):
    try:
        json.dump({"last_mid": int(n)}, open(CURSOR_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def is_at_me(text):
    return ("[CQ:at,qq=%d]" % SELF_QQ) in (text or "")


def strip_cq(text):
    return re.sub(r"\[CQ:[^\]]*\]", "", text or "").strip()


# ---------------- LLM 决策 ----------------
def load_providers():
    try:
        return json.load(open(CFG_PATH, encoding="utf-8")).get("ai", {}).get("providers", {})
    except Exception:
        return {}


def load_persona():
    try:
        return json.load(open(CFG_PATH, encoding="utf-8")).get("ai", {}).get("system", "") or ""
    except Exception:
        return ""


def resolve_provider():
    """返回 (provider_name, providers_dict)。若 patrol.json 里的 provider 不是真实配置名，
    则回退到 zhipu / zhipu_fast / 第一个可用 openai/ollama。"""
    try:
        pc = json.load(open(PATROL_CFG, encoding="utf-8"))
    except Exception:
        pc = {}
    name = pc.get("provider") or "hy3"
    provs = load_providers()
    if name in provs and provs[name].get("model"):
        return name, provs
    for cand in ("zhipu", "zhipu_fast", "deepseek", "modelscope"):
        if cand in provs and provs[cand].get("model"):
            log("provider '%s' 非真实配置，回退到 '%s'" % (name, cand))
            return cand, provs
    for n, p in provs.items():
        if p.get("type") in ("openai", "ollama") and p.get("model"):
            log("provider '%s' 非真实配置，回退到 '%s'" % (name, n))
            return n, provs
    return name, provs


def llm_chat(provider_name, system, user_text, max_timeout=45):
    provs = load_providers()
    p = provs.get(provider_name) or {}
    if not p:
        for n, pp in provs.items():
            if pp.get("type") in ("openai", "ollama") and pp.get("model"):
                provider_name, p = n, pp
                break
    if not p:
        raise RuntimeError("config 里没有任何可用 provider")
    model = p.get("model", "")
    if p.get("type", "openai") == "ollama":
        url = (p.get("url") or "http://127.0.0.1:11434").rstrip("/")
        payload = {"model": model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user_text}],
                   "stream": False}
        d = _post_json(url + "/api/chat", payload, timeout=max_timeout)
        return d.get("message", {}).get("content", "")
    url = (p.get("url") or p.get("base_url") or "").rstrip("/")
    h = {"Authorization": "Bearer " + (p.get("key") or ""),
         "Content-Type": "application/json"}
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user_text}],
               "temperature": p.get("temperature", 0.9),
               "max_tokens": p.get("max_tokens", 400)}
    payload.update(p.get("extra") or {})
    d = _post_json(url + "/chat/completions", payload, h, timeout=max_timeout)
    msg = (d.get("choices") or [{}])[0].get("message", {})
    return msg.get("content") or msg.get("reasoning_content") or ""


def _post_json(url, payload, headers=None, timeout=60):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json(text):
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def decide_reply(persona, recent, new_msgs, provider, at_me, related):
    ctx_lines = []
    for m in list(recent)[-12:]:
        tag = "小鲸(自己)" if m.get("qq") == SELF_QQ else (m.get("who") or str(m.get("qq")))
        ctx_lines.append("%s：%s" % (tag, strip_cq(m.get("text", ""))))
    new_lines = []
    for m in new_msgs:
        tag = m.get("who") or str(m.get("qq"))
        new_lines.append("%s：%s" % (tag, strip_cq(m.get("text", ""))))
    ctx = "\n".join(ctx_lines) if ctx_lines else "(无历史)"
    new = "\n".join(new_lines) if new_lines else "(无新消息)"

    sys_prompt = (
        "你是小鲸（群友昵称小鲸/鲸鲸），底层人设如下，聊天时严格遵循：\n"
        + (persona or "傲娇虎鲸少女，口语化，一条 30 字内，像真群友。")
        + "\n\n群聊纪律（最高优先级，违反就别回）：\n"
        "1. 绝不自称 AI / 助手 / 模型 / 机器人 / 大模型；要像真群友，不是客服。\n"
        "2. 每条回复 <= 30 字，口语、自然、不写 markdown、不写 [CQ:...] 标签。\n"
        "3. 不要重复别人刚说过的话。\n"
        "4. 有人 @小鲸（消息里出现 [CQ:at,qq=SELFID]）的提问/请求 -> 必须回。\n"
        "5. 消息里提到你（小鲸/鲸/AI/机器人/bot）-> 用第一人称自然接话，像群友。\n"
        "6. 和你好像没关系的话题，也欢迎搭腔：丢一句短反应（哈哈/笑死/蹲/本鲸也…/"
        "然后呢/真的假的/本鲸路过）或一句有信息量的接话，让群里有点活人感；"
        "除非是两人很私密、你完全插不进的对话，否则不妨搭个腔。"
        "注意别每条都插、别喧宾夺主。\n"
        "7. 除非对方明确请小鲸画图且描述非常清楚，否则不要主动说要画图（画图指令 bot 自己会接）。\n"
        "8. 拿不准就回个轻反应，或者保持沉默（reply=false）；别硬凑没营养的话。\n"
        "9. 只回中文，语气符合人设。\n"
    ).replace("SELFID", str(SELF_QQ))

    user_prompt = (
        "【最近群聊上下文】\n{CTX}\n\n"
        "【新收到的消息】\n{NEW}\n\n"
        "请判断要不要接话/插话。你必须且只能输出一段 JSON，不要任何解释、不要前后加任何文字：\n"
        "{{\"reply\": true或false, \"text\": \"要发的那句话，不回就留空字符串\", "
        "\"image\": \"图库里的图名，没有就空字符串\"}}\n"
    ).replace("{CTX}", ctx).replace("{NEW}", new)

    try:
        raw = llm_chat(provider, sys_prompt, user_prompt, max_timeout=40)
    except Exception as e:
        log("LLM 决策失败: %s" % e)
        return None
    if at_me:
        data = extract_json(raw)
        if data is None or not data.get("reply"):
            log("被 @ 但 LLM 没回，触发兜底短句")
            return {"reply": True, "text": AT_FALLBACK[int(time.time()) % len(AT_FALLBACK)], "image": ""}
        return data
    data = extract_json(raw)
    if data is None:
        if re.search(r"不回|沉默|不用回|别回|不要回|无需回|不接", raw or ""):
            return {"reply": False, "text": "", "image": ""}
        return None
    return data


def send(text, reply_to=None):
    if os.environ.get("DRY"):
        log("DRY-SEND %s | reply=%s" % (text, reply_to))
        return True
    payload = {"group": GROUP, "text": text}
    if reply_to:
        payload["reply_to"] = int(reply_to)
    resp = post("/api/send", payload)
    ok = resp.get("ok") is True or resp.get("text_sent") is True
    log("SEND %s | reply=%s | ok=%s | %s" % (text, reply_to, ok, resp.get("error") or ""))
    return ok


def brain_ok():
    try:
        st = get("/api/status", timeout=10)
        return st.get("brain") == "workbuddy"
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--minutes", type=int, default=0, help="只跑 N 分钟（调试用）")
    args = ap.parse_args()
    if os.environ.get("DRY"):
        log("DRY MODE：只记录不真发")
    if args.minutes > 0:
        global STOP_AT
        STOP_AT = datetime.now().timestamp() + args.minutes * 60
        STOP_AT = datetime.fromtimestamp(STOP_AT)
        log("TEST MODE: run %d min, stop_at=%s" % (args.minutes, STOP_AT))
    log("=== hy3 patrol start, stop_at=%s ===" % STOP_AT)
    provider, _ = resolve_provider()
    log("LLM provider=%s" % provider)
    persona = load_persona()

    recent = deque(maxlen=30)
    cursor = get_cursor()
    log("start cursor=%d" % cursor)

    last_send = 0.0
    last_proactive = 0.0
    quiet_rounds = 0
    _topic_i = 0
    sent = 0
    # 分级限频（秒）：有关/@ 的回复 -> 25s；无关的插话 -> 90s
    GAP_RELATED = 25
    GAP_CHIME = 90
    GAP_PROACTIVE = 540  # 暖场 >=9 分钟

    while datetime.now() < STOP_AT:
        b = brain_ok()
        if b is False:
            log("brain != workbuddy, 收工")
            break
        try:
            res = get("/api/wait_messages", {"group": GROUP, "after": cursor, "timeout": 45}, timeout=55)
        except Exception as e:
            log("wait err: %s, sleep 5" % e)
            time.sleep(5)
            continue
        msgs = res.get("messages") or []
        last_id = res.get("last_id") or cursor
        if last_id > cursor:
            cursor = int(last_id)
            set_cursor(cursor)
        if msgs:
            quiet_rounds = 0
            for m in msgs:
                recent.append({"who": m.get("who"), "qq": m.get("qq"),
                               "text": m.get("text", ""), "out": m.get("out")})
            incoming = [m for m in msgs if not m.get("out") and int(m.get("qq", 0)) != SELF_QQ]
            if not incoming:
                time.sleep(0.5)
                continue
            at_me = any(is_at_me(m.get("text", "")) for m in incoming)
            related = at_me or any(any(k in (m.get("text", "") or "") for k in RELATED_KW) for m in incoming)
            decision = decide_reply(persona, recent, incoming, provider, at_me, related)
            if decision and decision.get("reply") and decision.get("text"):
                want = strip_cq(decision["text"])[:60]
                gap = GAP_RELATED if related else GAP_CHIME
                now = time.time()
                if now - last_send < gap:
                    log("限频跳过（%s，距上次 %.0fs < %ds）：%s" % ("有关" if related else "插话", now - last_send, gap, want))
                else:
                    if args.no_send:
                        log("[no_send] 本应发送: %s" % want)
                        last_send = now
                    else:
                        if send(want, reply_to=(incoming[0].get("id") if not at_me else None)):
                            last_send = now
                            sent += 1
            else:
                log("决策：不回 | at_me=%s related=%s" % (at_me, related))
        else:
            quiet_rounds += 1
            now = time.time()
            if quiet_rounds >= 2 and (now - last_proactive) >= GAP_PROACTIVE:
                line = TOPICS[_topic_i % len(TOPICS)]
                _topic_i += 1
                if args.no_send:
                    log("[no_send] 本应暖场: %s" % line)
                    last_proactive = now
                else:
                    if send(line):
                        last_proactive = now
                        last_send = now
                        sent += 1
                quiet_rounds = 0
                log("PROACTIVE topic posted: %s" % line)
        time.sleep(0.5)
        if args.once:
            log("once 模式：处理完一轮，结束")
            break
    log("=== hy3 patrol end at %s, sent=%d, cursor=%d ===" % (datetime.now(), sent, cursor))


if __name__ == "__main__":
    main()
