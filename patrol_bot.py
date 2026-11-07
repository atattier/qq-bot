# -*- coding: utf-8 -*-
"""小鲸常驻巡逻脚本（独立于 WorkBuddy 的自动大脑）。

用法：
  start_patrol.bat          正常常驻巡逻（永久循环，你关窗口才停）
  python patrol_bot.py      同上
  python patrol_bot.py --once          只跑一轮 wait 就退出（调试用）
  python patrol_bot.py --once --no-send 只决策不真发（干跑验证链路）

设计要点：
  - 直接打 bot 本地接口（127.0.0.1:8088/api/*），复用 config.json 里配好的 provider
    密钥（只读，绝不写回、绝不打印明文）。
  - 只在 brain == "workbuddy" 时才发言；其余时间安静待命并推进游标，不抢班。
  - 长轮询 wait_messages：阻塞等 45 秒，消息一到立刻返回 -> 秒级感知。
  - 逐条决策走 LLM（默认 zhipu_fast 免费档），人设每轮从 config 现读，面板换人设自动跟。
  - 游标落 state/wb_poll.json，中途被关也不丢消息。
纯标准库，无第三方依赖。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
POLL_PATH = BASE / "state" / "wb_poll.json"
LOG_PATH = BASE / "patrol.log"

# 给 stdout/stderr 强制 UTF-8（Windows 控制台默认 GBK，中文会乱码）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- bot 本地接口 ----------------
def load_token():
    for f in (BASE / "state" / "bridge-token.json", BASE / "state" / "bridge_token.json"):
        try:
            if f.exists():
                t = json.loads(f.read_text(encoding="utf-8")).get("token", "")
                if t:
                    return t
        except Exception:
            pass
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8")).get("bridge_token", "")
    except Exception:
        return ""


TOKEN = load_token()


def _bot_get(path, params=None, timeout=60):
    url = "http://127.0.0.1:8088" + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"X-Bridge-Token": TOKEN or ""})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _bot_post(path, payload, timeout=60):
    req = urllib.request.Request(
        "http://127.0.0.1:8088" + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Bridge-Token": TOKEN or "",
                 "X-QQ-Source": "patrol"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_status():
    return _bot_get("/api/status")


def wait_messages(gid, after, timeout):
    return _bot_get("/api/wait_messages",
                    {"group": gid, "after": after, "timeout": timeout},
                    timeout=timeout + 10)


def send_msg(gid, text, image="", reply_to=0):
    return _bot_post("/api/send",
                     {"group": gid, "text": text, "image": image or "", "reply_to": reply_to or 0})


# ---------------- 游标 ----------------
def load_cursor():
    try:
        return int(json.loads(POLL_PATH.read_text(encoding="utf-8")).get("last_mid", 0))
    except Exception:
        return 0


def save_cursor(mid):
    try:
        POLL_PATH.parent.mkdir(parents=True, exist_ok=True)
        POLL_PATH.write_text(json.dumps({"last_mid": int(mid)}, ensure_ascii=False),
                             encoding="utf-8")
    except Exception as e:
        log("游标写回失败（不影响巡逻）: %s" % e)


# ---------------- LLM 决策 ----------------
def load_providers():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8")).get("ai", {}).get("providers", {})
    except Exception:
        return {}


def load_persona():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8")).get("ai", {}).get("system", "") or ""
    except Exception:
        return ""


def llm_chat(provider_name, system, user_text, max_timeout=60):
    provs = load_providers()
    p = provs.get(provider_name) or {}
    if not p:
        # 退化：随便挑一个能用的
        for n, pp in provs.items():
            if pp.get("type") in ("openai", "ollama") and pp.get("model"):
                provider_name, p = n, pp
                break
    if not p:
        raise RuntimeError("config 里没有任何可用 provider")
    model = p.get("model", "")
    if p.get("type", "openai") == "ollama":
        url = (p.get("url") or "http://127.0.0.1:11434").rstrip("/")
        payload = {"model": model, "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user_text}],
                   "stream": False}
        d = _post_json(url + "/api/chat", payload, timeout=p.get("timeout", 180))
        return d.get("message", {}).get("content", "")
    url = (p.get("url") or p.get("base_url") or "").rstrip("/")
    h = {"Authorization": "Bearer " + (p.get("key") or ""),
         "Content-Type": "application/json"}
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user_text}],
               "temperature": p.get("temperature", 0.9),
               "max_tokens": p.get("max_tokens", 500)}
    payload.update(p.get("extra") or {})
    d = _post_json(url + "/chat/completions", payload, h, timeout=max_timeout)
    msg = (d.get("choices") or [{}])[0].get("message", {})
    # content 为空别拿 reasoning_content 充数（=思考过程，发出去=漏思考 2026-10-01）
    t = msg.get("content") or ""
    t = re.sub(r"<think>.*?</think>\s*", "", t, flags=re.S | re.I)
    return re.sub(r"<think>.*\Z", "", t, flags=re.S | re.I).strip()


def _post_json(url, payload, headers=None, timeout=60):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strip_cq(text):
    return re.sub(r"\[CQ:[^\]]*\]", "", text or "").strip()


def extract_json(text):
    if not text:
        return None
    t = text.strip()
    # 去掉可能的 ```json ... ``` 代码围栏
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


def decide_reply(persona, self_id, recent, new_msgs, provider, at_me):
    """返回一个 dict {"reply": bool, "text": str, "image": str} 或 None（解析失败）。"""
    ctx_lines = []
    for m in list(recent)[-12:]:
        tag = "小鲸(自己)" if m.get("qq") == self_id else (m.get("who") or str(m.get("qq")))
        ctx_lines.append("%s：%s" % (tag, strip_cq(m.get("text", ""))))
    new_lines = []
    for m in new_msgs:
        tag = m.get("who") or str(m.get("qq"))
        new_lines.append("%s：%s" % (tag, strip_cq(m.get("text", ""))))
    ctx = "\n".join(ctx_lines) if ctx_lines else "(无历史)"
    new = "\n".join(new_lines) if new_lines else "(无新消息)"

    sys_prompt = (
        "你是小鲸（群友昵称小鲸/鲸鲸），底层人设如下，聊天时严格遵循：\n"
        + (persona or "温柔黏人、软糯口语、称呼群友为哥哥。")
        + "\n\n群聊纪律（最高优先级，违反就别回）：\n"
        "1. 绝不自称 AI / 助手 / 模型 / 机器人；不像客服，要像真群友。\n"
        "2. 每条回复 <= 30 字，口语、自然、不写 markdown、不写 [CQ:...] 标签。\n"
        "3. 不要重复别人刚说过的话。\n"
        "4. 两人聊天进行中、纯灌水、看不懂的梗 -> 保持沉默（reply=false）。\n"
        "5. 有人 @小鲸（消息里出现 [CQ:at,qq=SELFID]）的提问/请求 -> 必须回。\n"
        "6. 明显的接话点（提问、玩梗、聊到你懂的领域）-> 可以接一句，要有信息量或有趣。\n"
        "7. 除非描述非常清楚且对方明确请小鲸画图，否则不要主动说要画图（画图指令 bot 自己会接）。\n"
        "8. 只回中文，语气符合人设；拿不准就沉默。\n"
    ).replace("SELFID", str(self_id))

    user_prompt = (
        "【最近群聊上下文】\n{CTX}\n\n"
        "【新收到的消息】\n{NEW}\n\n"
        "请判断要不要接话。你必须且只能输出一段 JSON，不要任何解释、不要前后加任何文字：\n"
        "{{\"reply\": true或false, \"text\": \"要发的那句话，不回就留空字符串\", "
        "\"image\": \"图库里的图名，没有就空字符串\"}}\n"
    ).replace("{CTX}", ctx).replace("{NEW}", new)

    try:
        raw = llm_chat(provider, sys_prompt, user_prompt, max_timeout=40)
    except Exception as e:
        log("LLM 决策失败: %s" % e)
        return None
    if at_me:  # 被 @ 时 LLM 仍可能判不回，但 @ 必回是硬纪律，给个兜底
        data = extract_json(raw)
        if data is None or not data.get("reply"):
            log("被 @ 但 LLM 没回，触发兜底短句")
            return {"reply": True, "text": "在的在的，哥哥说~", "image": ""}
        return data
    data = extract_json(raw)
    if data is None:
        # 没吐 JSON：识别到「不回/沉默」类措辞也按不回复处理，避免误发
        if re.search(r"不回|沉默|不用回|别回|不要回|无需回|不接", raw or ""):
            return {"reply": False, "text": "", "image": ""}
        return None
    return data


# ---------------- 主循环 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一轮 wait 就退出")
    ap.add_argument("--no-send", action="store_true", help="只决策不真发（干跑）")
    args = ap.parse_args()

    # 巡逻专属可调项（不放 config.json，避免碰密钥）
    patrol_cfg = {}
    pfile = BASE / "state" / "patrol.json"
    if pfile.exists():
        try:
            patrol_cfg = json.loads(pfile.read_text(encoding="utf-8"))
        except Exception:
            pass
    provider = patrol_cfg.get("provider") or "zhipu_fast"
    poll_timeout = int(patrol_cfg.get("poll_timeout") or 45)
    per_run_cap = int(patrol_cfg.get("per_run_cap") or 12)
    group_id = patrol_cfg.get("group_id")

    log("巡逻脚本启动 | provider=%s poll=%ss cap=%d once=%s no_send=%s"
        % (provider, poll_timeout, per_run_cap, args.once, args.no_send))

    recent = deque(maxlen=30)
    cursor = load_cursor()
    if cursor == 0:  # 首次：建基线
        try:
            st = get_status()
            gid0 = group_id or (st.get("groups") or [None])[0]
            h = _bot_get("/api/history", {"group": gid0, "limit": 1})
            cursor = h.get("last_id", 0)
            save_cursor(cursor)
            log("首次基线 cursor=%s" % cursor)
        except Exception as e:
            log("建基线失败: %s" % e)

    sent_this_run = 0
    while True:
        try:
            st = get_status()
            if not st.get("ok"):
                log("bot 状态异常，10s 后重试")
                time.sleep(10)
                continue
            self_id = st.get("self_id")
            gid = group_id or (st.get("groups") or [None])[0]
            brain = st.get("brain")
            persona = load_persona()

            if brain != "workbuddy":
                # 待命：仍推进游标，不发言、不抢班
                w = wait_messages(gid, cursor, poll_timeout)
                if w.get("ok") and w.get("messages"):
                    cursor = w.get("last_id", cursor)
                    save_cursor(cursor)
                time.sleep(2)
                if args.once:
                    log("once 模式：brain=%s 非 workbuddy，结束" % brain)
                    break
                continue

            # 当班
            w = wait_messages(gid, cursor, poll_timeout)
            if not w.get("ok"):
                time.sleep(5)
                continue
            msgs = w.get("messages", [])
            last = w.get("last_id", cursor)
            # 先把游标推到最新（防重复处理），再决策
            if last and last != cursor:
                cursor = last
                save_cursor(cursor)

            if not msgs:
                if args.once:
                    log("once 模式：本轮超时无新消息，结束")
                    break
                continue

            # 过滤：只看真人新消息
            incoming = [m for m in msgs if not m.get("out") and m.get("qq") != self_id]
            for m in msgs:
                recent.append({"who": m.get("who"), "qq": m.get("qq"),
                               "text": m.get("text", ""), "out": m.get("out")})
            if not incoming:
                if args.once:
                    log("once 模式：本轮无真人新消息，结束")
                    break
                continue

            at_me = any("[CQ:at,qq=%s]" % self_id in (m.get("text") or "") for m in incoming)
            decision = decide_reply(persona, self_id, recent, incoming, provider, at_me)
            log("决策=%s" % decision)
            if decision and decision.get("reply") and decision.get("text"):
                if sent_this_run >= per_run_cap:
                    log("已达本班上限 %d 条，本轮沉默" % per_run_cap)
                else:
                    text = strip_cq(decision["text"])[:60]
                    if args.no_send:
                        log("[no_send] 本应发送: %s" % text)
                    else:
                        r = send_msg(gid, text, image=decision.get("image", "") or "")
                        log("发送结果=%s" % r.get("ok"))
                        sent_this_run += 1
                        time.sleep(2)
            if args.once:
                log("once 模式：处理完一轮，结束")
                break
        except KeyboardInterrupt:
            log("收到 Ctrl+C，退出")
            break
        except Exception as e:
            log("循环异常（继续）: %s" % e)
            time.sleep(5)


if __name__ == "__main__":
    main()
