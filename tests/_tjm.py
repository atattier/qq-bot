# -*- coding: utf-8 -*-
"""离线集成验证：加载 bot 模块（不启动服务），检查 JM 接入是否完整。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bot

print("=== 1. 模块加载 ===")
print("  jm_bot 导入:", "OK" if bot.jm_bot is not None else "失败(!!)")
print("  available:", bot.jm_bot.available())

print()
print("=== 2. KNOBS 注册 ===")
jmk = [k for k in bot.KNOBS if str(k.get("key", "")).startswith("jm.")]
for k in jmk:
    print("  %-22s group=%-6s type=%-5s default=%s" % (k["key"], k.get("group"), k.get("type"), k.get("default")))
assert len(jmk) == 4, "应该有 4 条 jm 旋钮，实际 %d" % len(jmk)

print()
print("=== 3. 配置读取 ===")
print("  _jm_cfg():", bot._jm_cfg())
print("  _jm_base():", bot._jm_base())

print()
print("=== 4. 口令识别 ===")
for t in [".jm 472537", "jm 472537", ".jmzip 472537", "闲聊一句话"]:
    print("  %-16r -> %s" % (t, bot.jm_bot.parse_cmd(t)))

print()
print("=== 4.5 随机口令识别（2026-10-09 加） ===")
for t, want in [(".jm随机", "fav"), (".jm收藏", "fav"), ("jm fav", "fav"),
                (".jm热", "rank"), (".jm热门", "rank"), ("jm 榜", "rank"),
                (".jmfav", "fav"), (".jm 472537", None), (".jmzip 472537", None),
                ("随机画一张", None), (".jm随机 123", None), ("", None)]:
    got = bot.jm_bot.parse_random_cmd(t)
    print("  %-14r -> %r" % (t, got))
    assert got == want, "随机口令 %r 应为 %r，实际 %r" % (t, want, got)
# 收藏缓存必须可用（缓存是 tools/_jm_fav_browser.py 抓的）
fr = bot.jm_bot.fav_rows()
print("  收藏缓存:", "缺失" if fr is None else "%d 本" % len(fr))
if not fr:
    print("SKIP: 收藏缓存 state/jm_favorites.json 未生成（先用 tools/_jm_fav_browser.py 抓一次）")
    sys.exit(0)
import random as _rd
_r0 = _rd.choice(fr)[0]
assert str(_r0).isdigit(), "收藏 ID 应为纯数字，实际 %r" % _r0
print("  抽样:", _r0)

print()
print("=== 4.6 已发去重（2026-10-10 加：随机/推荐不重发） ===")
# ⚠️ 去重记录写 state/jm_sent.json，测试**不能污染主人真数据** ⇒ 临时换路径，最后还原
import tempfile as _tf, os as _os
_real_sent = bot.jm_bot.SENT_JSON
_tmp_sent = _os.path.join(_tf.mkdtemp(), "jm_sent.json")
bot.jm_bot.SENT_JSON = _tmp_sent
try:
    jm = bot.jm_bot
    jm.reset_sent()
    print("  洗牌口令:", jm.parse_random_cmd(".jm重置"), jm.parse_random_cmd("jm洗牌"),
          jm.parse_random_cmd(".jm reset"))
    assert jm.parse_random_cmd(".jm重置") == "reset"
    assert jm.parse_random_cmd(".jm洗牌") == "reset"
    assert jm.parse_random_cmd(".jm reset") == "reset"

    # 收藏夹：连抽到抽完，必须本本不同（去重生效的话正好是一轮全排列）
    rows = jm.fav_rows()
    got = []
    for _ in range(len(rows)):
        aid, title, note = jm.pick_random("fav")
        got.append(aid)
        jm.mark_sent("fav", aid, title)
    print("  收藏夹 %d 本连抽 %d 次，去重后不重复：%s" % (
        len(rows), len(got), "OK" if len(set(got)) == len(rows) else "!! 有重复"))
    assert len(set(got)) == len(rows), "每本都应只出现一次，实际 %d/%d" % (len(set(got)), len(rows))
    assert got[-1] and jm.sent_count("fav") == len(rows)

    # 抽完再抽 -> 自动洗牌，说明里要写明
    aid2, _, note2 = jm.pick_random("fav")
    print("  抽完再抽:", note2)
    assert "重新洗牌" in note2, "全发过后应提示重新洗牌，实际：%s" % note2
    assert aid2 in [r[0] for r in rows]

    # 月榜同样去重（不打网络，直接喂假数据）
    _orig_rank = jm.rank_rows
    jm.rank_rows = lambda *a, **k: [("%d" % i, "t%d" % i) for i in range(5)]
    jm.reset_sent("rank")
    rk = []
    for _ in range(5):
        aid3, t3, _n3 = jm.pick_random("rank")
        rk.append(aid3)
        jm.mark_sent("rank", aid3, t3)
    print("  月榜 5 本连抽:", rk, "| 去重:", "OK" if len(set(rk)) == 5 else "!! 有重复")
    assert len(set(rk)) == 5, "月榜也应不重复，实际 %r" % rk
    jm.rank_rows = _orig_rank

    # 洗牌后计数归零
    jm.reset_sent()
    assert jm.sent_count() == 0, "洗牌后应清空，实际 %d" % jm.sent_count()
    print("  洗牌后已发计数:", jm.sent_count())
finally:
    pass
# ⚠️ SENT_JSON 仍指向临时文件，§5 的入队也会记一笔；跑完一起还原，别写脏主人数据

print()
print("=== 5. 闸门（不发消息，只验逻辑） ===")
sent = []
_orig = bot.send_group
bot.send_group = lambda gid, text, group_hint=None: (sent.append(text), True)[1]
try:
    cfg = bot._jm_cfg()
    print("  enabled=%s allow_group=%s allow_private=%s recall=%ss" % (
        cfg.get("enabled"), cfg.get("allow_group"), cfg.get("allow_private"), cfg.get("recall_seconds")))

    # 非 jm 口令 -> 不接管
    r = bot.handle_jm_cmd(-12345, "今天天气不错", False, False, 1001)
    print("  普通消息接管?", r, "(应为 False)")
    assert r is False

    # 私聊 .jm -> 接管并回复（会把任务塞队列，测试完清掉）
    r = bot.handle_jm_cmd(-12345, ".jm 472537", False, False, 1001)
    print("  私聊 .jm 接管?", r, "| 回复:", sent[-1] if sent else "(无)")
    assert r is True and bot._jm_q.qsize() == 1
    bot._jm_q.get()
    bot._jm_q.task_done()

    # 群聊 .jm（默认允许）
    # ⚠️ 别信 config 现状 —— 主人可能在 /admin 关过 allow_group（2026-10-09 实测就是关的），
    #    那样任务不会入队，下面的 _jm_q.get() 会**永久阻塞**。测试自备状态。
    _ag0 = bot.CFG.setdefault("jm", {}).get("allow_group")
    bot.CFG["jm"]["allow_group"] = True
    bot._jm_state["last"].pop(123450001, None)      # 别被上一条口令的冷却挡住
    r = bot.handle_jm_cmd(123450001, ".jm 472538", False, True, 1002, "某人")
    print("  群聊 .jm 接管?", r, "| 回复:", sent[-1][:40] if sent else "(无)")
    assert r is True
    assert bot._jm_q.qsize() == 1, "群聊口令应入队（allow_group 已强制开）"
    bot._jm_q.get()
    bot._jm_q.task_done()
    bot.CFG["jm"]["allow_group"] = _ag0             # 还原主人设置

    # 冷却应拦住第二条
    r = bot.handle_jm_cmd(123450001, ".jm 999999", False, True, 1003, "某人")
    print("  冷却拦截:", sent[-1][:30] if sent else "(无)")
    assert r is True and bot._jm_q.qsize() == 0

    # owner_only 打开后，非管理员被拒
    bot.CFG.setdefault("jm", {})["owner_only"] = False
    bot.CFG.setdefault("jm", {})["owner_only"] = True
    r = bot.handle_jm_cmd(-999, ".jm 472537", False, False, 2001)
    print("  owner_only 拒绝:", sent[-1])
    bot.CFG["jm"]["owner_only"] = False

    # 随机口令走完整闸门 + 入队（收藏缓存就在本地，不碰网络）
    bot._jm_state["last"].pop(-555, None)
    r = bot.handle_jm_cmd(-555, ".jm随机", False, False, 3001)
    print("  .jm随机 接管?", r, "| 回复:", sent[-1][:46] if sent else "(无)")
    assert r is True and bot._jm_q.qsize() == 1, "随机口令应入队 1 个任务"
    job = bot._jm_q.get(); bot._jm_q.task_done()
    assert str(job["aid"]).isdigit(), "随机任务应解析成数字 ID，实际 %r" % job["aid"]
    print("  入队任务 aid =", job["aid"])
    # 入队成功 = 应记进「已发」，下次不会再抽到它
    assert job["aid"] in bot.jm_bot.sent_ids("fav"), "入队后应记进已发，实际 %r" % bot.jm_bot.sent_ids("fav")
    print("  已记录已发:", sorted(bot.jm_bot.sent_ids("fav")))

    # .jm重置：只洗牌，不下单、不占冷却
    bot._jm_state["last"].pop(-555, None)
    q0 = bot._jm_q.qsize()
    r = bot.handle_jm_cmd(-555, ".jm重置", False, False, 3001)
    print("  .jm重置 接管?", r, "| 回复:", sent[-1][:36] if sent else "(无)")
    assert r is True and bot._jm_q.qsize() == q0, "洗牌不该入队"
    assert bot.jm_bot.sent_count() == 0, "洗牌后已发应清空，实际 %d" % bot.jm_bot.sent_count()
finally:
    bot.send_group = _orig
    bot.jm_bot.SENT_JSON = _real_sent      # 还原真记录文件（上面全在临时文件里跑）

print()
print("ALL JM INTEGRATION CHECKS PASSED")
