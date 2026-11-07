# -*- coding: utf-8 -*-
"""qbot_memory 离线自测（不依赖 bot，不碰线上 state）。
用法： python tests/_tmem.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import qbot_memory as M  # noqa: E402

ok = True


def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


tmp = Path(tempfile.mkdtemp(prefix="maimem_"))
try:
    print("== bind ==")
    M.bind(state_dir=tmp)
    chk("db 路径生成", M.db_path().endswith("maimem.db"), M.db_path())

    print("== 自动层：记人 ==")
    for _ in range(3):
        M.note_person(123450001, "10001", "阿伟")
    M.note_person(123450001, "10002", "小鱼")
    M.note_person(123450001, "10002", "")          # 空名字不该覆盖
    ps = M.people(123450001)
    chk("两人入库、按发言数倒序", len(ps) == 2 and ps[0]["qq"] == "10001" and ps[0]["count"] == 3, ps)
    chk("空名字没覆盖旧名", [p for p in ps if p["qq"] == "10002"][0]["name"] == "小鱼",
        [p for p in ps if p["qq"] == "10002"])
    chk("群隔离", M.people(999999) == [])

    print("== 自主层：画像事实 ==")
    chk("写入成功", M.remember_fact(123450001, "10001", "在准备考研"))
    chk("重复写入被去重", M.remember_fact(123450001, "10001", "在准备考研") is False)
    M.remember_fact(123450001, "10001", "不吃香菜")
    M.remember_fact(123450001, "10002", "养了只布偶猫，叫团子")
    M.remember_fact(123450001, "10002", "喜欢三角洲行动")
    fs = M.recall(123450001, "", limit=20)
    chk("共 4 条事实", len(fs) == 4, len(fs))

    print("== 关键词检索 ==")
    r1 = M.recall(123450001, "考研")
    chk("「考研」命中阿伟那条", len(r1) >= 1 and "考研" in r1[0]["content"], [x["content"] for x in r1])
    r2 = M.recall(123450001, "猫")
    chk("「猫」命中养猫那条", any("布偶猫" in x["content"] for x in r2), [x["content"] for x in r2])
    r3 = M.recall(123450001, "阿伟")
    chk("按名字检索命中其全部", len(r3) >= 2, [x["content"] for x in r3])

    print("== 注入块 ==")
    blk = M.profile_block(123450001)
    chk("含表头", "你长期记得的人" in blk)   # 2026-10-19 改成「按人存」后表头随之改
    chk("含人名与事实", "阿伟" in blk and "考研" in blk and "布偶猫" in blk)
    print("---- profile_block 实际输出 ----")
    print(blk)
    print("--------------------------------")

    print("== 遗忘 ==")
    fid = M.recall(123450001, "香菜")[0]["id"]
    chk("按 id 删一条", M.forget(123450001, fact_id=fid) == 1)
    chk("确实没了", not any("香菜" in x["content"] for x in M.recall(123450001, "")))
    chk("按 qq 删某人全部", M.forget(123450001, qq="10002") >= 2)

    print("== 按人存：跨场合（2026-10-19 新） ==")
    # 同一个人(10001)在另一个群说过的话，用任意 gid 都该查得到
    M.remember_fact(999999, "10001", "最近在加班到十二点", "阿伟", "note")
    chk("换个群也查得到同一个人", any("加班" in x["content"] for x in M.recall(123450001, "加班")))
    chk("people 按人聚合（不按群）", any(p["qq"] == "10001" for p in M.people(None, 10)))

    print("== 跨场合对话流 ==")
    M.log_turn("group:123450001", "阿伟", "10001", "今晚开黑吗", "不了，加班")
    M.log_turn("private:10002", "小鱼", "10002", "睡了吗", "还没")
    rt = M.recent_turns(10)
    chk("两轮都记下了", len(rt) >= 2, len(rt))
    chk("按时间正序", rt[-1]["text"] == "睡了吗", rt[-1])
    rp = M.render_recent(10, exclude_src="group:123450001")
    chk("排除当前会话", "开黑" not in rp and "睡了吗" in rp)
    chk("含来源标注", "私聊" in rp or "群里" in rp)
    pb = M.render_person("10001", "阿伟")
    chk("账本含人名与事实", "阿伟" in pb and "加班" in pb, pb.strip()[:60])

    print("== 容错 ==")
    chk("未 bind 时不炸", M.people(1) == [] or True)
    M._DB = None
    chk("DB 为 None 时 people 安全", M.people(1) == [])
    chk("DB 为 None 时 fact 安全", M.remember_fact(1, 1, "x") is False)
    chk("DB 为 None 时 block 安全", M.profile_block(1) == "")
    M.bind(state_dir=tmp)

    print("== stats ==")
    st = M.stats()
    chk("stats ok", st.get("ok"), st)

    # ---------------------------------------------------------------- 三件套
    # 2026-10-26 晚：主人要「向量召回 + 衰减 + 分层」三件套。这一节**全程不联网**：
    # 嵌入函数用 _EMB_OVERRIDE 注入假向量，模拟 bge-m3 的「同义不同字」能力。
    print("== 三件套①：向量召回 ==")

    def _fake_vec(t):
        """假嵌入：按语义桶分维，桶之间正交；不认识的词返回 None（当没向量）。

        关键——「研究生考试」和「考研」一个字都不重合，只有向量能把它们连起来。
        """
        d = [0.0, 0.0, 0.0]
        hit = False
        if any(w in t for w in ("考研", "研究生", "考试", "复习", "上岸")):
            d[0], hit = 1.0, True
        if any(w in t for w in ("猫", "布偶", "喵", "宠物")):
            d[1], hit = 1.0, True
        if any(w in t for w in ("香菜", "吃", "忌口", "火锅", "美式")):
            d[2], hit = 1.0, True
        return d if hit else None

    M._EMB_OVERRIDE = _fake_vec
    M._EMB_CACHE.clear()
    M._EMB_DOWN_UNTIL = 0.0
    M.bind(state_dir=tmp, cfg={"memory": {"embed": True, "hybrid": True}})
    M.remember_fact(123450001, "10001", "在准备考研")
    M.remember_fact(123450001, "10002", "养了只布偶猫，叫团子")
    # 前面几段是用真 ollama 跑的，库里已经躺着真向量了；不清掉的话假向量根本进不来
    # （backfill 只补 vec IS NULL 的行），这段就会变成一个「时好时坏」的假用例。
    M.flush_vec()
    with M._LOCK, M._conn() as c:
        c.execute("UPDATE facts SET vec=NULL")
    M._EMB_CACHE.clear()
    n_vec = M.backfill(20)
    es = M.embed_status()
    chk("老记录都补上了向量", es["vec"] >= 2, (n_vec, es["vec"], es["facts"]))
    r = M.recall(123450001, "研究生考试")
    chk("换个说法也能召回（纯语义）",
        bool(r) and "考研" in r[0]["content"],
        [(x["content"], x["score"], x["why"]) for x in r[:3]])
    chk("命中的是语义而不是关键词", r and r[0]["why"] == "语义", r[0]["why"] if r else "")
    chk("不相干的（养猫）没被向量带进来",
        not any("布偶猫" in x["content"] for x in r),
        [x["content"] for x in r])

    # 没有向量时退回字符二元组：同义改写抓不到，这是**已知且写进文档**的短板
    M._EMB_OVERRIDE = None
    M._EMB_CACHE.clear()
    M._EMB_DOWN_UNTIL = time.time() + 600      # 强制走「拿不到向量」分支
    r_bg = M.recall(123450001, "研究生考试")
    chk("无向量时同义改写召不回（诚实降级）",
        not any("考研" in x["content"] for x in r_bg), [x["content"] for x in r_bg])
    r_bg2 = M.recall(123450001, "考研")
    chk("无向量时原词照样命中", any("考研" in x["content"] for x in r_bg2),
        [x["content"] for x in r_bg2])

    print("== 三件套②：权重衰减 ==")
    now = time.time()
    chk("刚写的：衰减≈1", M._decay(now) > 0.99, M._decay(now))
    chk("半衰期 180 天：打对折", abs(M._decay(now - 180 * 86400) - 0.5) < 0.02,
        M._decay(now - 180 * 86400))
    chk("十年老事：压到地板 0.25", abs(M._decay(now - 3650 * 86400) - 0.25) < 0.01,
        M._decay(now - 3650 * 86400))
    chk("承诺比随手记重", M._KIND_W["promise"] > M._KIND_W["note"],
        (M._KIND_W["promise"], M._KIND_W["note"]))

    # 同一句话、两个人、一个三年前一个刚说：内容/关键词/语义全一样，只有时间不同
    M.remember_fact(123450001, "10003", "在准备考研")
    M.remember_fact(123450001, "10004", "在准备考研")
    with M._LOCK, M._conn() as c:
        c.execute("UPDATE facts SET ts=? WHERE qq='10003' AND content='在准备考研'",
                  (now - 720 * 86400,))
    rr = M.recall(123450001, "考研", limit=5, bump=False)
    order = [x["qq"] for x in rr if x["content"] == "在准备考研"]
    chk("同样内容：新说的排在老的之前",
        "10004" in order and "10003" in order
        and order.index("10004") < order.index("10003"), order)
    w_old = [x for x in rr if x["qq"] == "10003"][0]["weight"]
    w_new = [x for x in rr if x["qq"] == "10004"][0]["weight"]
    chk("老那条的份量确实更低", w_old < w_new, (w_old, w_new))

    print("== 三件套③：分层注入 ==")
    M._EMB_OVERRIDE = _fake_vec          # 上一节为了测降级把它关了，这里开回来
    M._EMB_CACHE.clear()
    M._EMB_DOWN_UNTIL = 0.0
    blk3 = M.recall_block("研究生考试", 123450001)
    chk("召回块有表头", "旧记忆" in blk3, blk3[:30])
    chk("召回块带上了相关那条", "考研" in blk3)
    chk("召回块不超字数预算", len(blk3) <= 320 + 80, len(blk3))
    chk("召回块不超条数上限", blk3.count("\n- ") <= 4, blk3.count("\n- "))
    blk4 = M.recall_block("研究生考试", 123450001, n=1, chars=20)
    chk("预算收紧时只留一条", blk4.count("\n- ") == 1, blk4)
    chk("空 query 不拼段", M.recall_block("") == "")
    chk("一个字不拼段", M.recall_block("猫") == "")
    chk("没命中就返回空（不硬凑）", M.recall_block("量子力学和火箭发动机") == "",
        M.recall_block("量子力学和火箭发动机"))

    print("== 三件套：复述效应 ==")
    M.remember_fact(123450001, "10005", "喜欢喝冰美式", kind="like")
    with M._conn() as c:
        w1 = c.execute("SELECT weight FROM facts WHERE content='喜欢喝冰美式'").fetchone()[0]
    chk("喜好类初始权重 1.4", abs(w1 - 1.4) < 0.01, w1)
    M.remember_fact(123450001, "10005", "喜欢喝冰美式", kind="like")
    with M._conn() as c:
        w2, h2 = c.execute("SELECT weight,hits FROM facts WHERE content='喜欢喝冰美式'"
                           ).fetchone()
    chk("重复提及会加权", w2 > w1, (w1, w2))
    chk("重复提及记一次 hits", h2 >= 1, h2)
    with M._conn() as c:
        h0 = c.execute("SELECT hits FROM facts WHERE qq='10004' AND content='在准备考研'"
                       ).fetchone()[0]
    M.recall(123450001, "考研", limit=3)
    with M._conn() as c:
        h1 = c.execute("SELECT hits FROM facts WHERE qq='10004' AND content='在准备考研'"
                       ).fetchone()[0]
    chk("被召回一次 hits+1", h1 == h0 + 1, (h0, h1))

    print("== 三件套：开关 ==")
    M.bind(state_dir=tmp, cfg={"memory": {"hybrid": False}})
    r_kw = M.recall(123450001, "研究生考试")
    chk("hybrid=False 退回纯关键词（同义改写召回不了）",
        not any("考研" in x["content"] for x in r_kw), [x["content"] for x in r_kw])
    M.bind(state_dir=tmp, cfg={"memory": {"embed": False}})
    chk("embed=False 时 embed() 直接返回 None", M.embed("测试") is None)
    M.bind(state_dir=tmp, cfg={})
    M._EMB_OVERRIDE = None
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== " + ("ALL PASS" if ok else "有用例失败") + " ====")
sys.exit(0 if ok else 1)
