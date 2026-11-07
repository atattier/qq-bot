# -*- coding: utf-8 -*-
"""「测试的发言不要发群里」守卫（2026-10-30 第七修）。

主人原话：「测试的发言不要发群里不要」

## 为什么要单独一份用例（两个真事故，都是我把假消息变成真发言）

1. **21:32:52**：我为了验证「攒够整段才判」，往 `POST /onebot` 注入了三条假群消息
   （「这板子4K屏」「我笔记本才2.5K」「画质有点低」），bot 照单全收，
   还把「4K还得看面板色域，2.5K日常其实够用」**真发进了 123450001**。
   验证手段本身成了污染源。
2. **22:15~23:12**：回归进程跟线上 bot 共用 `config.json`，带图插话那条用例
   一路走到 `send_bubbles` ⇒ 群里连出五条「哥哥只有我主人一个，你就算了。」

## 本文件盯四件事

  A. `_test_mute` 的判据：测试进程 / test_mute 开关 / 演练态，任一命中就静音
  B. 静音**真的发生在唯一出口** `_api_send`（不是靠上层自觉打桩）
  C. `/onebot` 入口：来路不明的注入必须 403；`?drill=1` + token 才收，且收下就打演练态
  D. 逃生舱 `_MUTE_BYPASS` 是**显式**的、默认关（不能让后人随手开着跑）

全离线：不起服务、不联网、不发群（本文件自己就开着静音）。
"""
import os
import sys
import inspect
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

FAIL = []


def chk(name, got, want):
    if got != want:
        FAIL.append(name)
        print("FAIL %-52s got=%r want=%r" % (name, got, want))
    else:
        print("OK  " + name)


GID = 123450001

# ---------------- A. `_test_mute` 判据 ----------------
chk("A1/_test_mute 存在（唯一出口要用它）", callable(getattr(bot, "_test_mute", None)), True)

# A2：回归进程（本文件就跑在 QBOT_TEST=1 下）⇒ 必须静音
_os_env = os.environ.get("QBOT_TEST")
os.environ["QBOT_TEST"] = "1"
_mute, _why = bot._test_mute(GID)
chk("A2/测试进程一律静音", _mute, False)
chk("A3/原因说得出（日志能自证）", "测试" in _why, True)
if _os_env is None:
    os.environ.pop("QBOT_TEST", None)
else:
    os.environ["QBOT_TEST"] = _os_env

# A4：`test_mute` 配置开关（主人临时演练用）
_old_tm = bot.CFG.get("test_mute")
bot.CFG["test_mute"] = True
os.environ.pop("QBOT_TEST", None)
bot._IN_TEST = False                      # 假装是线上进程
_mute2, _why2 = bot._test_mute(GID)
chk("A4/test_mute 开着也静音", _mute2, False)
chk("A5/原因写明是演练模式", "test_mute" in _why2, True)
if _old_tm is None:
    bot.CFG.pop("test_mute", None)
else:
    bot.CFG["test_mute"] = _old_tm

# A6：线上进程 + 没开 test_mute + 这个会话没被注入 ⇒ **必须放行**
_mute3, _why3 = bot._test_mute(GID)
chk("A6/正常线上不该被静音（别一刀切）", _mute3, True)
chk("A7/放行时原因为空", _why3, "")

# A8~A11：演练态（注入过的会话静音，别的会话照发）
bot._INJECT.clear()
bot._mark_injected(GID, "测试注入", 60)
_mute4, _why4 = bot._test_mute(GID)
chk("A8/注入过的会话静音", _mute4, False)
chk("A9/原因说明是演练态", "演练" in _why4, True)
_other = 999000111
chk("A10/别的会话不受影响（只挡注入那一段）",
    bot._test_mute(_other)[0], True)
chk("A11/剩余秒数算得出来", bot._is_injected(GID) > 0, True)
bot._clear_injected(GID)
chk("A12/解掉演练态立刻恢复", bot._test_mute(GID)[0], True)

# A13：到期自动恢复（不能永久静音，否则主人以为 bot 坏了）
bot._mark_injected(GID, "x", 0.2)
time.sleep(0.35)
chk("A13/演练态过期自动恢复", bot._test_mute(GID)[0], True)
bot._clear_injected(GID)

# ---------------- B. 静音真的在 `_api_send` 里发生 ----------------
_src = inspect.getsource(bot._api_send)
chk("B1/_api_send 调用了 _test_mute", "_test_mute(" in _src, True)
chk("B2/静音发生在选接口之前（根本没构造 OneBot 调用）",
    _src.index("_test_mute(") < _src.index('action, params = "send_private_msg"'), True)

# 真的走一遍：静音时**一个 api 调用都不该发生**
_API_CALLS = []
_orig_api = bot.api
bot.api = lambda action, **kw: (_API_CALLS.append(action)
                                or {"status": "ok", "retcode": 0})
_orig_bypass = bot._MUTE_BYPASS["on"]
bot._MUTE_BYPASS["on"] = False
os.environ["QBOT_TEST"] = "1"
_r = bot._api_send(GID, [{"type": "text", "data": {"text": "测试发言"}}])
chk("B3/静音时不打 OneBot", _API_CALLS, [])
chk("B4/静音对调用方是「成功但不发」（别让它重试刷屏）",
    (_r.get("muted") is True) and (_r.get("status") == "ok"), True)

# 各种入口都要被同一道闸挡住（send_group / send_segments / send_image / send_reply）
for _name, _fn in (("send_group", lambda: bot.send_group(GID, "测试")),
                   ("send_segments", lambda: bot.send_segments(
                       GID, [{"type": "text", "data": {"text": "测试"}}]))):
    _API_CALLS.clear()
    _fn()
    chk("B5/%s 也被静音" % _name, _API_CALLS, [])

# 逃生舱打开时才真发（这就是 `_timgb64` / `_thuman` 用的口子）
bot._MUTE_BYPASS["on"] = True
_API_CALLS.clear()
bot._api_send(GID, [{"type": "text", "data": {"text": "x"}}])
chk("B6/逃生舱打开才真发", bool(_API_CALLS), True)
bot._MUTE_BYPASS["on"] = _orig_bypass
bot.api = _orig_api

# B7：逃生舱**默认必须是关的**（防后人随手开着跑）
chk("B7/_MUTE_BYPASS 默认关闭", bot._MUTE_BYPASS["on"], False)

# ---------------- C. `/onebot` 入口不认来路不明的注入 ----------------
chk("C1/_looks_like_napcat 存在", callable(getattr(bot, "_looks_like_napcat", None)), True)

# R4：本文件测的是“未配置 napcat_report_token”的旧兼容判据；
# 先清空，避免真实 config 以后启用了上报 token 导致假红。
_orig_report_token = bot.CFG.get("napcat_report_token")
bot.CFG["napcat_report_token"] = []


class _H(dict):
    """假的 headers（大小写不敏感，跟 http.server 一样）。"""

    def items(self):
        return super().items()


# 空事件 + 空头 ⇒ 拒收（这就是「随手打个 JSON 上来」的样子）
chk("C2/无头 + 空事件 ⇒ 拒收", bot._looks_like_napcat(_H(), {}), False)
chk("C3/无头 + 只有 message ⇒ 拒收（老注入脚本就是长这样）",
    bot._looks_like_napcat(_H(), {"message": [{"type": "text",
                                               "data": {"text": "介绍这家公司"}}]}),
    False)
# 以下是“未配置 napcat_report_token”的旧兼容分支；R4 严格模式见 _treporttoken.py
chk("C4/User-Agent: NapCat ⇒ 放行",
    bot._looks_like_napcat(_H({"User-Agent": "NapCat/1.0"}), {}), True)
chk("C5/带 X-Signature ⇒ 放行",
    bot._looks_like_napcat(_H({"X-Signature": "abc"}), {}), True)

# C6~C9：**主判据 = NapCat 上报 token 比对**
_NAPCAT_TOKEN = str(bot.CFG.get("token") or "").strip()
_backup_tok = bot.CFG.get("token")
bot.CFG["token"] = "SECRET_NAPCAT_TOK"          # 钉一个已知值，别依赖真实 config
chk("C6/带对的上报 token ⇒ 放行",
    bot._looks_like_napcat(_H({"Authorization": "Bearer SECRET_NAPCAT_TOK"}), {}), True)
chk("C7/带错的 token ⇒ 拒收（冒充）",
    bot._looks_like_napcat(_H({"Authorization": "Bearer WRONG"}), {}), False)
_SELF_BAK = bot.SELF_ID
bot.SELF_ID = 123450004
# 🔴 这条是本次收紧的关键：**填对 bot 的 QQ 号也不行**（QQ 号是群里的公开信息，
#    单靠它等于没闸 —— 我实测「填对号+无头」第一版就放行了）。
chk("C8/填对 self_id 但没上报 token ⇒ 拒收（QQ 号是公开的，不能当凭据）",
    bot._looks_like_napcat(_H(), {"self_id": 123450004, "post_type": "message"}), False)
chk("C9/self_id 对不上 ⇒ 拒收", bot._looks_like_napcat(_H(), {"self_id": 999999}), False)

# C10~C11：本机**没配**上报 token 时退到 self_id（别把主人的真消息全掐了）
bot.CFG["token"] = ""
chk("C10/没配 token 时 self_id 一致 ⇒ 放行（兜底，别漏真消息）",
    bot._looks_like_napcat(_H(), {"self_id": 123450004, "post_type": "message"}), True)
bot.SELF_ID = 0        # 冷启动：还没连上 NapCat
chk("C11/冷启动（SELF_ID=0）时带 self_id ⇒ 放行（否则重启后第一条全丢）",
    bot._looks_like_napcat(_H(), {"self_id": 123450004, "post_type": "message"}), True)
bot.SELF_ID = _SELF_BAK
bot.CFG["token"] = _backup_tok
chk("C12/结构完整但无身份证明 ⇒ 拒收（关键：不能再靠「长得像」放行）",
    bot._looks_like_napcat(_H(), {"post_type": "message", "group_id": GID,
                                  "time": int(time.time())}), False)
# C13~C16：源码级 —— 入口真的接上了这两道（不然上面全是空转）
_src_post = inspect.getsource(bot.Handler.do_POST)
chk("C13/入口调用了 _looks_like_napcat", "_looks_like_napcat(" in _src_post, True)
chk("C14/入口认 drill 参数", "drill" in _src_post, True)
chk("C15/drill 必须过 bridge token", "_bridge_ok()" in _src_post, True)
chk("C16/被拒的注入会记账（谁打进来能查）", "来路不明" in _src_post, True)

if _orig_report_token is None:
    bot.CFG.pop("napcat_report_token", None)
else:
    bot.CFG["napcat_report_token"] = _orig_report_token

print("\nRESULT:", "PASS" if not FAIL else "FAIL (%d 项) %s" % (len(FAIL), FAIL))
sys.exit(1 if FAIL else 0)
