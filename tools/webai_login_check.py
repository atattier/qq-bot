# -*- coding: utf-8 -*-
"""豆包网页版「到底登录没登录」体检 —— 一键答案，不弹窗、不发消息。

    python tools/webai_login_check.py

背景（2026-10-25 事故）
-----------------------
主人按提示登录了，但 bot 一直报「豆包未登录」。排查下来是**两个坑叠加**：

  ① `webai_login_doubao.bat` 里 `start "" chrome.exe --user-data-dir=...`
     在 Chrome **已经开着**的时候会忽略 `--user-data-dir`，把 URL 丢给
     正在运行的那个实例 ⇒ 主人登进了自己的日常 profile，bot 的 profile 没变。
  ② 光看 cookie 库会误判：`sessionid` / `sid_guard` 都存在、过期时间也刷新了，
     **但服务端认不认是另一回事**。实测解码证据：豆包接口
     `get_web_anon_id` 回的仍是 `uid:"anon"`（anon = anonymous 访客）。

所以判据只能看**服务端怎么说**。本脚本按可靠度从高到低做三件事：
  1. 打开 `https://www.doubao.com/chat/`，抓 `get_web_anon_id` 之类接口的响应，
     看回的是真 uid 还是 `anon`；
  2. 看右上角**可见的**「登录」按钮在不在（在 = 未登录）；
  3. 看页面里有没有「最近对话」条目 / 头像元素（有 = 登录过）。

**不发任何消息、不消耗额度、不改任何文件。** 全程 headless。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CFG = BASE / "config.json"


def _webai_dir():
    """从 config.json 的 webai.dir 拿 webai-agent 目录（跟 bot 用同一份配置）。"""
    try:
        c = json.loads(CFG.read_text(encoding="utf-8"))
        d = str((c.get("webai") or {}).get("dir") or "").strip()
        if d and Path(d).is_dir():
            return Path(d)
    except Exception:  # noqa: BLE001
        pass
    return None


SESSION_JS = """() => {
  const btns = [...document.querySelectorAll('button')]
    .filter(b => (b.innerText || '').trim() === '登录');
  const vis = btns.filter(b => {
    const r = b.getBoundingClientRect();
    return b.offsetParent !== null && r.width > 1 && r.height > 1;
  });
  const txt = (document.body.innerText || '');
  return {
    url: location.href,
    login_btn_visible: vis.length,
    login_btn_box: vis.length ? (() => { const r = vis[0].getBoundingClientRect();
      return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)}; })() : null,
    inputs: document.querySelectorAll('[contenteditable="true"],textarea').length,
    avatarish: document.querySelectorAll(
      '[class*="avatar"],[class*="Avatar"],[class*="user-info"],[class*="userInfo"]').length,
    // 「最近」下面有对话标题 = 用过；登录后侧栏才会有历史
    has_recent: /最近|最近对话/.test(txt),
    text_head: txt.slice(0, 300).replace(/\\n+/g, ' | '),
  };
}"""


async def main():
    d = _webai_dir()
    if not d:
        print("[x] config.json 里的 webai.dir 无效（读不到 webai-agent 目录）")
        return 2
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    os.chdir(str(d))

    try:
        from config.settings import DOUBAO_PROFILE_DIR, DOUBAO_URL  # noqa: E402
        from browser.session_manager import SessionManager           # noqa: E402
    except Exception as e:  # noqa: BLE001
        print("[x] 导入 webai-agent 失败：%s" % str(e)[:160])
        return 2

    print("== 豆包登录体检 ==")
    print("  profile : %s" % DOUBAO_PROFILE_DIR)
    print("  webai   : %s" % d)
    print()

    api_hits = []          # (url, status, body)
    sm = SessionManager(DOUBAO_PROFILE_DIR, headless=True)
    page = await sm.start()
    state = {}
    try:
        async def on_resp(r):
            u = r.url
            if any(k in u for k in ("get_web_anon_id", "user/info", "user_info",
                                    "passport", "samantha/user", "account/info")):
                try:
                    body = (await r.text())[:400]
                except Exception:  # noqa: BLE001
                    body = ""
                api_hits.append((u[:110], r.status, body))

        page.on("response", on_resp)
        await page.goto(DOUBAO_URL, wait_until="domcontentloaded")
        await asyncio.sleep(8)
        try:
            state = await page.evaluate(SESSION_JS)
        except Exception as e:  # noqa: BLE001
            state = {"err": str(e)[:120]}
    finally:
        try:
            await sm.stop()
        except Exception:  # noqa: BLE001
            pass

    # ---- ① 服务端怎么说（最可靠）----
    # 🔴 判据不要用 `get_web_anon_id` 的 uid 是不是 "anon" —— 那接口**访客也会回一个
    #    真实数字 uid**（实测 `uid:"3735498366661992"`，那是设备/访客标识，不是账号）。
    #
    # ✅ 真正可靠的判据（2026-10-25 真机把三种响应都见过了）：
    #   · `passport/token/beat/web/`      → `error_code:0, description:"success"`
    #       这是**会话心跳**：心跳成功 = 登录态活着。**最优先看这个。**
    #   · `passport/doubao/security_token_beat/` → `message:"success"`
    #       同上，另一个心跳。
    #   · `passport/web/auth/get_oauth_token/` → 过期时才会回
    #       `{"data":{"error_code":1,"description":"会话过期，请重新登录"}}`
    #       ⚠️ 但会话**正常**时它也可能回 12009 之类的内部码，**不能一律当过期**
    #       （这就是 2026-10-25 已经登录成功却被误报「未登录」的原因）。
    #   所以：**心跳 success 优先于 get_oauth_token 的任何错误码**。
    beat_ok, expired, expired_desc, got_token = False, False, "", False
    for url, st, body in api_hits:
        try:
            j = json.loads(body)
        except Exception:  # noqa: BLE001
            continue
        data = j.get("data")
        desc = str((data or {}).get("description") or j.get("message") or "")
        if "token/beat" in url or "security_token_beat" in url:
            # 心跳：error_code 缺失/0 且 success 字样 → 活着
            code = (data or {}).get("error_code", 0) if isinstance(data, dict) else 0
            if str(j.get("message") or "").lower() == "success" or (
                    code in (0, None) and "success" in (desc or "").lower()):
                beat_ok = True
            elif "过期" in desc or "重新登录" in desc:
                expired, expired_desc = True, desc
        elif "get_oauth_token" in url and isinstance(data, dict):
            code = data.get("error_code")
            # 只有「明确说过期」才算过期；单独的 12009 之类不算（见上）
            if "过期" in desc or "重新登录" in desc:
                expired, expired_desc = True, desc
            elif not code and data:
                got_token = True
    if api_hits:
        print("-- 账号接口 --")
        for url, st, body in api_hits[:6]:
            short = url.split("?")[0]
            print("  %s %s" % (st, short))
            if body:
                print("     %s" % body[:150].replace("\n", " "))
        print()

    # ---- ② 页面长什么样 ----
    print("-- 页面状态 --")
    print("  url             : %s" % state.get("url", "?"))
    print("  可见「登录」按钮 : %s 处 %s" % (
        state.get("login_btn_visible", "?"),
        ("（%s）" % state.get("login_btn_box")) if state.get("login_btn_box") else ""))
    print("  输入框          : %s 个" % state.get("inputs", "?"))
    print("  头像/用户区元素 : %s 个" % state.get("avatarish", "?"))
    if state.get("err"):
        print("  读取报错        : %s" % state["err"])
    print()

    # ---- ③ 结论 ----
    # 优先级（从硬到软）：
    #   心跳 success  → 已登录（这是服务端主动确认会话活着，最硬）
    #   明说过期      → 未登录
    #   拿到 token    → 已登录
    #   页面按钮      → 兜底
    if beat_ok:
        logged = True
    elif expired:
        logged = False
    elif got_token:
        logged = True
    elif state.get("login_btn_visible") is not None:
        logged = (state.get("login_btn_visible") == 0)
    else:
        logged = False
    print("== 结论 ==")
    if beat_ok:
        print("  服务端会话心跳成功（token/beat = success）")
    if expired:
        print("  服务端 passport 说：%s" % expired_desc)
    elif got_token:
        print("  服务端已发 oauth token（会话有效）")
    print("  => %s" % ("**已登录**，可以在面板打开「网页识图」了" if logged
                       else "**未登录（会话已过期）** —— 跑 webai_login_doubao.bat 重新登录"))
    if not logged:
        print()
        print("  常见原因：① 登录时 Chrome 已经开着，--user-data-dir 被忽略了；")
        print("            ② 登进了自己日常的 profile，不是 bot 这个；")
        print("            ③ 登录后又过期了（豆包会周期性作废）。")
        print("  正确做法：关掉**所有** Chrome 窗口 -> 双击 webai_login_doubao.bat")
        print("            -> 登录 -> **关掉那个窗口** -> 再跑一次本脚本确认。")
    return 0 if logged else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
