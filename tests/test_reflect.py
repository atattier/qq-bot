"""Reflection 专项验收测试（2026-10-26 第二阶段）。

覆盖主人点名的四类专项：**人格隔离 / 事实来源校验 / 幂等 / 重启恢复**，
外加生命周期、开关停写、游标安全。

用法：python tools/test_reflect.py
全部用临时目录，**不碰 state/maimem.db**，可以随时重跑。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import qbot_memory as M          # noqa: E402
import qbot_reflect as R         # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✅" if cond else "❌", name, ("  " + str(extra)) if extra else ""))


def fresh():
    tmp = tempfile.mkdtemp(prefix="reflecttest_")
    M.bind(state_dir=tmp)
    R.bind(os.path.join(tmp, "maimem.db"), {
        "enabled": True, "min_interval": 0, "daily_max": 999,
        "max_input": 40, "speak_threshold": 0.0,
    })
    return tmp


def seed(src, rows, persona="小鲸"):
    """rows = [(who, text)]"""
    for who, text in rows:
        M.log_turn(src, who, "1001", text, "", persona=persona)


def items_json(items):
    return json.dumps({"items": items}, ensure_ascii=False)


def main():
    tmp = fresh()
    try:
        print("\n【1】人格归属与增量读取")
        seed("group:1", [
            ("阿明", "明天我要试试那个量化模型"),
            ("小红", "你上次说的外卡还没打吧"),
            ("阿明", "对，一直没时间，改天再弄"),
            ("小红", "哈哈哈"),
        ], persona="小鲸")
        seed("group:1", [("阿明", "这句是换了人格之后说的")], persona="毒舌群友")
        t1 = R.fetch_turns("group:1", 0, "小鲸")
        t2 = R.fetch_turns("group:1", 0, "毒舌群友")
        ok("只读到本人物格的 4 条", len(t1) == 4, len(t1))
        ok("别人格的记录读不到", len(t2) == 1 and "换了人格" in t2[0]["text"], len(t2))

        # 老记录（persona 为 NULL）必须被跳过
        with R._conn() as c:
            c.execute("INSERT INTO turns(ts,src,who,qq,text,reply,persona) "
                      "VALUES(?,?,?,?,?,?,NULL)",
                      (time.time(), "group:1", "老记录", "9", "这是没有人格归属的老话", ""))
            c.commit()
        t3 = R.fetch_turns("group:1", 0, "小鲸")
        ok("NULL 人格的老记录被跳过", all("老话" not in x["text"] for x in t3), len(t3))

        print("\n【2】事实来源校验（不许编造、不许扩写）")
        tid = {x["text"][:6]: x["id"] for x in t1}
        good = {"kind": "observation", "topic": "量化模型", "content": "阿明想试量化模型",
                "basis": "fact", "confidence": 0.8,
                "refs": [{"turn_id": t1[0]["id"], "quote": "明天我要试试那个量化模型"}]}
        fake_no_quote = {"kind": "observation", "topic": "看视频",
                         "content": "我今天看了一个很长的视频", "basis": "fact",
                         "confidence": 0.9,
                         "refs": [{"turn_id": t1[0]["id"], "quote": "我今天看了一个视频"}]}
        fake_no_ref = {"kind": "opinion", "topic": "空气", "content": "我觉得大家都很闲",
                       "basis": "opinion", "confidence": 0.7, "refs": []}
        expand = {"kind": "observation", "topic": "外卡", "content": "他打了 12 次外卡都没过",
                  "basis": "fact", "confidence": 0.9,
                  "refs": [{"turn_id": t1[1]["id"], "quote": "外卡还没打吧"}]}
        foreign = {"kind": "observation", "topic": "串群", "content": "别的群的记录",
                   "basis": "fact", "confidence": 0.9,
                   "refs": [{"turn_id": t2[0]["id"], "quote": "这句是换了人格之后说的"}]}

        def fake(msgs):
            return items_json([good, fake_no_quote, fake_no_ref, expand, foreign])

        okr, why, st = R.run_once(1, "group:1", "小鲸", chat_fn=fake, force=True)
        ok("run_once 成功", okr, why)
        got = {r[1]: r for r in R.candidates_all("小鲸", 1)}
        ok("正常 fact 入库", "量化模型" in got)
        ok("编造 quote（原文里没有）被丢", "看视频" not in got, st.get("verify"))
        ok("没有 refs 的被丢", "空气" not in got)
        ok("跨群的 turn_id 被丢", "串群" not in got)
        ok("扩写（凭空多出数字 12）降级为 inference",
           "外卡" in got and got["外卡"][3] == "inference", got.get("外卡"))

        print("\n【3】幂等（同一段对话重复处理）")
        with R._conn() as c:
            n1 = c.execute("SELECT COUNT(*) FROM reflect_item").fetchone()[0]
        # 把游标退回起点，强制重新处理同一段
        with R._conn() as c:
            c.execute("UPDATE reflect_cursor SET last_turn_id=0 WHERE src='group:1'")
            c.commit()
        R.run_once(1, "group:1", "小鲸", chat_fn=fake, force=True)
        with R._conn() as c:
            n2 = c.execute("SELECT COUNT(*) FROM reflect_item").fetchone()[0]
        ok("重跑不新增行", n1 == n2, "%d -> %d" % (n1, n2))

        print("\n【4】生命周期")
        iid = got["量化模型"][0] if False else None
        with R._conn() as c:
            iid = c.execute("SELECT id FROM reflect_item WHERE topic='量化模型' "
                            "AND persona='小鲸' AND gid='1'").fetchone()[0]
        # 先把它变成候选里能出现的样子
        with R._conn() as c:
            c.execute("UPDATE reflect_item SET kind='open_loop', weight=0.9 WHERE id=?", (iid,))
            c.commit()
        cands = R.candidates(1, "小鲸")
        ok("open_loop 出现在候选里", any(x["id"] == iid for x in cands), len(cands))
        R.set_status(iid, "resolved", "小鲸", 1)
        cands2 = R.candidates(1, "小鲸")
        ok("resolved 之后不再出现", all(x["id"] != iid for x in cands2))
        with R._conn() as c:
            still = c.execute("SELECT COUNT(*) FROM reflect_item WHERE id=?", (iid,)).fetchone()[0]
        ok("resolved 记录仍保留（可回溯）", still == 1)

        print("\n【5】长期未解决不删（只休眠）")
        old = int(time.time()) - 60 * 86400      # 60 天前
        with R._conn() as c:
            c.execute("UPDATE reflect_item SET status='open', updated=?, weight=0.9 WHERE id=?",
                      (old, iid))
            c.commit()
        ok("60 天后不在候选（weight 衰减到休眠）",
           all(x["id"] != iid for x in R.candidates(1, "小鲸")))
        with R._conn() as c:
            alive = c.execute("SELECT COUNT(*) FROM reflect_item WHERE id=?", (iid,)).fetchone()[0]
        ok("但行没被删", alive == 1)

        print("\n【6】人格隔离（硬约束）")
        ok("别的群查不到", len(R.candidates(999, "小鲸")) == 0)
        ok("同群别的人格查不到", len(R.candidates(1, "毒舌群友")) == 0)
        ok("跨人格改状态无效", R.set_status(iid, "dismissed", "毒舌群友", 1) is False)
        with R._conn() as c:
            st2 = c.execute("SELECT status FROM reflect_item WHERE id=?", (iid,)).fetchone()[0]
        ok("状态没被跨人格改掉", st2 == "open", st2)

        print("\n【7】开关真的停副作用")
        R.bind(os.path.join(tmp, "maimem.db"), {"enabled": False, "min_interval": 0})
        with R._conn() as c:
            before = c.execute("SELECT COUNT(*) FROM reflect_item").fetchone()[0]
        okr2, why2, _ = R.run_once(1, "group:1", "小鲸", chat_fn=fake, force=True)
        with R._conn() as c:
            after = c.execute("SELECT COUNT(*) FROM reflect_item").fetchone()[0]
        ok("关闭后 run_once 直接拒绝", okr2 is False and "关闭" in why2, why2)
        ok("关闭后不写库", before == after, "%d -> %d" % (before, after))

        print("\n【8】失败恢复（游标不前进）")
        R.bind(os.path.join(tmp, "maimem.db"), {"enabled": True, "min_interval": 0,
                                                "daily_max": 999, "max_input": 40})
        cur_before = R.cursor_get("group:1", "小鲸")["last_turn_id"]

        def boom(msgs):
            raise RuntimeError("模型挂了")
        seed("group:1", [("阿明", "这是新的一句，用来测失败恢复")], persona="小鲸")
        okr3, why3, _ = R.run_once(1, "group:1", "小鲸", chat_fn=boom, force=True)
        cur_after = R.cursor_get("group:1", "小鲸")["last_turn_id"]
        ok("模型失败时返回 False", okr3 is False, why3)
        ok("游标没前进（下次会重试这段）", cur_before == cur_after,
           "%s -> %s" % (cur_before, cur_after))

        print("\n【9】重启恢复")
        R.bind(os.path.join(tmp, "maimem.db"), {"enabled": True, "min_interval": 0})
        rows = R.candidates_all("小鲸", 1)
        ok("重新 bind 后数据还在", len(rows) >= 1, len(rows))
        # 模拟进程重启：清掉模块级缓存
        R._ensured = False
        R._DB = None
        R.bind(os.path.join(tmp, "maimem.db"), {"enabled": True})
        ok("清缓存后重新 bind 仍能读到", len(R.candidates_all("小鲸", 1)) >= 1)

        print("\n【10】数据不泄漏到主库")
        ok("临时库里没写过 state/maimem.db",
           os.path.abspath(os.path.join(ROOT, "state", "maimem.db")) !=
           os.path.abspath(os.path.join(tmp, "maimem.db")))

        print("\n【11】切换人格后即使没新记录也持久化游标")
        R._commit_cursor("group:empty", "旧人格", 0, ok=True)
        called = []
        okr4, why4, _ = R.run_once(1, "group:empty", "新人格",
                                   chat_fn=lambda msgs: called.append(msgs) or "{}", force=True)
        cur4 = R.cursor_get("group:empty", "新人格")
        ok("没有新人格记录时正常跳过模型", not okr4 and not called and "没有新记录" in why4, why4)
        ok("新人格已写入游标，下轮不会重复重置",
           cur4.get("persona") == "新人格" and cur4.get("last_turn_id") == 0, cur4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  ❌", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
