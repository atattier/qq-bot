"""插件网页编辑 + 插件市场 专项验收（2026-10-26）。

两条链路一起验：
  A. 网页编辑：开关 / 参数 / 提示词正文 —— 「网页点一下 → 文件真变了 → 下一句话真用新的」
  B. 插件市场：market/ 里一键装进 skills/、一键卸回 _removed/，**不用重启**就能在网页上看见

用法：python tools/test_skill_edit.py
全部在**临时目录副本**上跑，不碰真技能包、不碰 config.json、不碰真 market/、不写真 state。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["QBOT_TEST"] = "1"

import qbot_skills as S          # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✅" if cond else "❌", name, ("  " + str(extra)) if extra else ""))


def main():
    import bot                                  # 先导入：KNOBS 是导入时建的
    import qbot_market as M
    real_save = bot.save_config
    real_knobs = list(bot.KNOBS)
    real_cfg = {k: bot.CFG.get(k) for k in ("skills", "market")}
    bot.save_config = lambda: True              # 别把临时配置写回真 config.json

    # 临时目录：skills 副本 + market 副本，配置全指过去（bot.CFG 是全局唯一那份）
    tmp = tempfile.mkdtemp(prefix="skilledit_")
    shutil.copytree(os.path.join(ROOT, "skills"), os.path.join(tmp, "skills"))
    shutil.copytree(os.path.join(ROOT, "market"), os.path.join(tmp, "market"))
    bot.CFG["skills"] = {"enabled": True, "dir": "skills", "en": {}, "settings": {},
                         "prompt_max_chars": 3000, "section_max_chars": 1200}
    bot.CFG["market"] = {"enabled": True, "dir": "market"}
    cfg = bot.CFG
    S.bind(cfg=cfg, base=tmp)
    M.bind(cfg=cfg, base=tmp)
    S.reload()

    # 用临时目录重建插件旋钮（真 KNOBS 里的指向真文件，先摘掉）
    bot.KNOBS[:] = [k for k in bot.KNOBS
                    if not (str(k.get("group", "")) == bot._SKILL_GROUP
                            or str(k.get("key", "")).startswith("skills.prompt.")
                            or str(k.get("key", "")).startswith("skills.settings."))]
    knobs = bot._skill_knobs()
    bot.KNOBS.extend(knobs)

    try:
        print("\n[1] 结构：一个组装全部插件，每项带 skill / kind")
        ok("全部插件旋钮在同一个组", all(k["group"] == bot._SKILL_GROUP for k in knobs),
           bot._SKILL_GROUP)
        sids = sorted({k["skill"] for k in knobs if k.get("skill")})
        ok("每个包都有旋钮", sids == sorted(x["id"] for x in S.scan()), sids)
        for sid in sids:
            ks = [k for k in knobs if k.get("skill") == sid]
            ok("%s 有总开关" % sid,
               any(k["kind"] == "toggle" and k["key"] == "skills.en." + sid for k in ks))
            ok("%s 有可编辑提示词" % sid, any(k["kind"] == "prompt" for k in ks))
        ok("没有重复的 enabled 开关",
           not any(k["key"].startswith("skills.settings.") and k["key"].endswith(".enabled")
                   for k in knobs))

        print("\n[2] 提示词：读得到原文")
        pk = [k for k in knobs if k["key"].startswith("skills.prompt.petpet.")][0]
        before = pk["get"]()
        ok("get 返回原文", bool(before) and "什么时候用" in before, "%d 字" % len(before))

        print("\n[3] 提示词：网页改 → skill.json 真变了")
        newtext = "【什么时候用】测试文本：只在有人喊「捏一下」时动手。\n\n【怎么调】照旧。"
        res = bot.knob_set(pk["key"], newtext, privileged=True)
        ok("knob_set 成功", res[0], res[1][:60])
        folder = S.get("petpet")["path"]
        path = os.path.join(folder, "skill.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        sec = [s for s in data["prompt"]["sections"] if s.get("id") == "petpet-playbook"][0]
        ok("skill.json 里内容已更新", sec["content"] == newtext)
        ok("其他字段没被动", data["name"] == "捏脸表情包"
           and data["version"].startswith("1.")
           and data["configSchema"]["defaultTemplate"]["type"] == "enum")
        ok("重扫后 get 读到新值", pk["get"]() == newtext)
        ok("新提示词进了大脑的提示词段", "测试文本" in S.prompt_block())
        baks = sorted(f for f in os.listdir(folder) if ".bak-" in f)
        ok("写了备份", len(baks) >= 1, ",".join(baks[-2:]))
        ok("没留 .tmp 残渣", not any(f.endswith(".tmp") for f in os.listdir(folder)))

        print("\n[4] 提示词：坏输入不许写")
        r = bot.knob_set(pk["key"], "   ", privileged=True)
        ok("空内容被拒", not r[0], r[1][:40])
        r = bot.knob_set("skills.prompt.petpet.不存在的片段", "x", privileged=True)
        ok("未知片段被拒", not r[0], r[1][:40])
        with open(path, encoding="utf-8") as f:
            ok("被拒后文件没变",
               json.load(f)["prompt"]["sections"][0]["content"] == newtext)

        print("\n[5] 提示词：没改就不写文件")
        m1 = os.stat(path).st_mtime_ns
        r = bot.knob_set(pk["key"], newtext, privileged=True)
        ok("同一份内容返回成功", bool(r[0]), r[1][:40])
        ok("mtime 没动（没白写盘）", os.stat(path).st_mtime_ns == m1)

        print("\n[6] 参数：改的是 config，不动技能包")
        mk = [k for k in knobs if k["key"] == "skills.settings.petpet.maxPerHour"][0]
        ok("初值来自清单", mk["get"]() == 20, mk["get"]())
        r = bot.knob_set(mk["key"], "5", privileged=True)
        ok("knob_set 成功", r[0], r[1][:40])
        ok("settings_of 反映新值", S.settings_of("petpet")["maxPerHour"] == 5)
        ok("落到 config 的 skills.settings",
           cfg["skills"]["settings"]["petpet"]["maxPerHour"] == 5)
        with open(path, encoding="utf-8") as f:
            ok("skill.json 里的默认值没被改", json.load(f)["settings"]["maxPerHour"] == 20)

        print("\n[7] 开关：一个开关同时管两处（is_on + 包内 enabled）")
        sk = [k for k in knobs if k["key"] == "skills.en.petpet"][0]
        r = bot.knob_set(sk["key"], "关", privileged=True)
        ok("knob_set 成功", r[0], r[1][:40])
        ok("is_on 变 False", S.is_on("petpet") is False)
        ok("包内 settings.enabled 同步 False", S.settings_of("petpet")["enabled"] is False)
        ok("提示词段从大脑里消失", "捏脸表情包" not in S.prompt_block())
        bot.knob_set(sk["key"], "开", privileged=True)
        ok("再开回来", S.is_on("petpet") is True and S.settings_of("petpet")["enabled"] is True)

        print("\n[8] 预算：写超长会被截断，后台能看见两个数")
        cfg["skills"]["section_max_chars"] = 300
        long_txt = "很长的提示词。" * 200
        bot.knob_set(pk["key"], long_txt, privileged=True)
        blk = S.prompt_block()
        ok("注入时按单段预算截断", long_txt not in blk and "…" in blk)
        budget = [k for k in bot.KNOBS if k["key"] == "skills.section_max_chars"][0]
        ok("后台「单段预算」读到同一个值", bot.knob_get(budget) == 300, bot.knob_get(budget))

        print("\n[9] 网页接口形状：插件卡 + 市场卡")
        conf = bot.action_config()
        grp = [g for g in conf["groups"] if g["group"] == bot._SKILL_GROUP][0]
        ok("插件卡标题是「已安装插件」", grp.get("title") == "已安装插件", grp.get("title"))
        # 2026-11-05 U3：插件卡 = 沙盒技能（_skill_meta 跳过带 err / 无 sections 的）
        # + 内置卡（meme 的卡是 _refresh_skill_knobs 末尾注入的，不在 skills/ 扫描里）。
        # 所以断言「沙盒技能必须全在」，多出的内置卡（meme）单独点名。
        _expect_ids = sorted(sk["id"] for sk in S.scan()
                             if not sk.get("err") and sk.get("sections"))
        _meta_ids = sorted(p.get("id") for p in (grp.get("plugins") or []))
        ok("插件卡带 plugins 表头", all(x in _meta_ids for x in _expect_ids)
           and "meme" in _meta_ids,
           "%s vs 沙盒%s" % (_meta_ids, _expect_ids))
        ok("插件卡占满一行", grp.get("min_span") == 3)
        ok("表头带工具/参数/提示词计数",
           all(k in grp["plugins"][0] for k in ("id", "name", "desc", "tools", "on",
                                                "params", "prompts")))
        mk_grp = [g for g in conf["groups"] if g["group"] == "插件市场"][0]
        ok("有插件市场卡", bool(mk_grp), len(mk_grp.get("market") or []))
        ok("市场卡排在最后", conf["groups"][-1]["group"] == "插件市场")

        print("\n[10] 插件市场：装 / 卸 / 拒绝")
        before_ids = sorted(x["id"] for x in S.scan())
        ok("装之前没有 no-fabricate", "no-fabricate" not in before_ids, before_ids)
        r = M.install("no-fabricate")
        ok("安装成功", r[0], r[1][:50])
        ok("文件进了 skills/", os.path.isdir(os.path.join(tmp, "skills", "no-fabricate")))
        ok("技能加载器立刻看得到", "no-fabricate" in [x["id"] for x in S.scan()])
        all_items = [i for g in bot.action_config()["groups"] for i in g["items"]]
        ok("旋钮表不用重启就更新",
           any(i["key"] == "skills.en.no-fabricate" for i in all_items))
        r = M.install("no-fabricate")
        ok("重复安装被拒", not r[0], r[1][:40])
        r = M.uninstall("petpet")
        ok("内置包不许卸", not r[0], r[1][:40])
        r = M.uninstall("no-fabricate")
        ok("卸载成功", r[0], r[1][:50])
        ok("目录已移走", not os.path.isdir(os.path.join(tmp, "skills", "no-fabricate")))
        ok("移到 _removed 而不是删掉",
           os.path.isdir(os.path.join(tmp, "market", "_removed"))
           and any("no-fabricate" in d for d in os.listdir(os.path.join(tmp, "market", "_removed"))))
        ok("卸完列表恢复", sorted(x["id"] for x in S.scan()) == before_ids)
        ok("市场列表带 installed 标记",
           all("installed" in x for x in M.list_all()))

        # ── [11] 菲比表情包：本机图库 ──────────────────────────────────
        print("\n[11] 菲比表情包：本机图库（不联网、不写素材目录）")
        import qbot_skill_tools as T
        pics = os.path.join(tmp, "pics")
        os.makedirs(pics)
        # 拿真实素材里最小的几张「菲比」，保证是真图（发送前要认魔数）
        src_dir = os.path.join(ROOT, "images", "emoji")
        cands = sorted((os.path.join(src_dir, f) for f in os.listdir(src_dir)
                        if "菲比" in f and f.lower().endswith(".gif")),
                       key=lambda p: os.path.getsize(p))
        real = cands[:3]
        ok("能取到真实菲比素材", len(real) == 3, [os.path.basename(p) for p in real])
        # 改名成有语义的名字，方便验「关键词优先命中」（内容还是真 gif）
        for src, dst in zip(real, ("干饭菲比.gif", "生气菲比.gif", "无语菲比.gif")):
            shutil.copy2(src, os.path.join(pics, dst))
        shutil.copy2(real[0], os.path.join(pics, "无关梗图.gif"))
        with open(os.path.join(pics, "超大菲比.gif"), "wb") as f:
            f.write(b"GIF89a" + b"\0" * (5 * 1024 * 1024))
        before_files = sorted(os.listdir(pics))

        bot.CFG["skills"]["settings"]["fibi-memes"] = {
            "folder": pics, "maxMB": 4, "maxPerHour": 50, "maxPerDay": 200,
            "dedupe": True, "match": "菲比"}
        sent_paths = []
        real_bind = (T.CFG, T.STATE, T.BASE, T._SEND_IMAGE)
        T.bind(cfg=cfg, state_dir=os.path.join(tmp, "st"), base=tmp,
               send_image_file=lambda c, p, cap="": (sent_paths.append(p), True)[1])

        idx = T._fibi_index(force=True)
        names = [x["stem"] for x in idx]
        ok("索引只收名字带「菲比」的", all("菲比" in n for n in names), names)
        ok("干扰图被排除", "无关梗图" not in names)
        ok("超大图被排除", "超大菲比" not in names)
        ok("索引条数 = 3 张小图", len(idx) == 3, len(idx))

        c0, _n0 = T.fibi_candidates("干饭", want=5, cid=1, shuffle=False)
        ok("关键词按文件名优先命中",
           bool(c0) and all("干饭" in x["stem"] for x in c0), [x["stem"] for x in c0][:3])

        r1 = T.call("fibi-memes", "send", cid=100, keyword="", count=1)
        r2 = T.call("fibi-memes", "send", cid=100, keyword="", count=1)
        r3 = T.call("fibi-memes", "send", cid=100, keyword="", count=1)
        ok("三次都能发出去", all(r[0] for r in (r1, r2, r3)),
           [r[1][:18] for r in (r1, r2, r3)])
        ok("同会话不重发同一张", len(set(sent_paths)) == 3, len(set(sent_paths)))
        ok("发出去的文件真实存在", all(os.path.isfile(p) for p in sent_paths))
        after_files = sorted(os.listdir(pics))
        ok("素材目录一个文件都没多（派生图写缓存，不写图库）",
           before_files == after_files,
           [x for x in after_files if x not in before_files])

        bot.CFG["skills"]["settings"]["fibi-memes"]["maxPerHour"] = 1
        q1 = T.call("fibi-memes", "send", cid=200, keyword="", count=1)
        q2 = T.call("fibi-memes", "send", cid=200, keyword="", count=1)
        ok("每小时额度生效", q1[0] and not q2[0], q2[1][:36])
        bot.CFG["skills"]["settings"]["fibi-memes"]["maxPerHour"] = 50

        S.set_on("fibi-memes", False)
        off = T.call("fibi-memes", "send", cid=300, keyword="", count=1)
        ok("技能关了就不发", not off[0], off[1][:24])
        S.set_on("fibi-memes", True)

        clean, marks = bot.take_fibi_markers(1, "好耶\n[[菲比:开心]]\n")
        ok("标记被取出来", marks == ["开心"], marks)
        ok("正文里不残留标记", "[[" not in clean and clean.strip() == "好耶", repr(clean))
        ok("梗图标记不会被菲比正则吃掉",
           bot.take_fibi_markers(1, "[[梗图:生气]]")[1] == [])

        T.CFG, T.STATE, T.BASE, T._SEND_IMAGE = real_bind
        ok("原生工具已登记", "fibi-memes" in T.NATIVE, sorted(T.NATIVE))
        ok("提示词里告诉了模型怎么发",
           "qq_fibi_send" in T.USAGE.get("fibi-memes", "")
           and "[[菲比:" in T.USAGE.get("fibi-memes", ""))

        # ── [12] AstrBot 只读目录 ─────────────────────────────────────
        print("\n[12] AstrBot 官方市场：只读、缓存优先、失败不动旧数据")
        M.bind(cfg=cfg, base=tmp, state_dir=os.path.join(tmp, "st"))
        M._AS["data"] = None
        d0 = M.astrbot_search("翻译")
        ok("没缓存也不报错（当空目录）", d0["ok"] and d0["total"] == 0, d0["total"])
        ok("没缓存时状态是「该同步」", M.astrbot_status()["stale"] is True)

        fake = {"at": 1, "source": "test", "items": [
            {"id": "a", "name": "翻译插件", "desc": "把中文翻成英文", "author": "甲",
             "repo": "https://github.com/x/a", "category": "工具", "tags": [], "version": ""},
            {"id": "b", "name": "签到插件", "desc": "每日签到 + 语音播报", "author": "乙",
             "repo": "https://github.com/x/b", "category": "娱乐", "tags": [], "version": ""},
            {"id": "c", "name": "点歌", "desc": "语音点歌", "author": "丙",
             "repo": "https://github.com/x/c", "category": "娱乐", "tags": [], "version": ""}]}
        os.makedirs(os.path.join(tmp, "st", "market"), exist_ok=True)
        with open(os.path.join(tmp, "st", "market", "astrbot.json"), "w",
                  encoding="utf-8") as f:
            json.dump(fake, f, ensure_ascii=False)
        M._AS["data"] = None
        d1 = M.astrbot_search("翻译")
        ok("按名字/简介能搜到", d1["matched"] == 1 and d1["items"][0]["name"] == "翻译插件")
        d2 = M.astrbot_search("语音 签到")
        ok("多个词是「任一命中」而不是全都要",
           d2["matched"] == 2, [x["name"] for x in d2["items"]])
        ok("命中词多的排前面", d2["items"][0]["name"] == "签到插件",
           [x["name"] for x in d2["items"]])
        ok("搜不到就是空（不编）", M.astrbot_search("不存在的词")["matched"] == 0)
        ok("结果带仓库链接", d1["items"][0]["repo"].startswith("https://github.com/"))
        ok("AstrBot 的包不会混进可安装列表",
           not any(x["id"] in ("a", "b", "c") for x in M.list_all()))

        real_src = M.ASTRBOT_SOURCES
        M.ASTRBOT_SOURCES = ("http://127.0.0.1:9/none.json",)
        r = M.astrbot_sync(timeout=2)
        M.ASTRBOT_SOURCES = real_src
        ok("网络不通时同步失败但不抛", not r[0], r[1][:40])
        ok("失败不动旧缓存", M.astrbot_search("翻译")["matched"] == 1)

        # ── [12b] 筛选框 + 翻页（主人：「加筛选框」「为啥只显示 24 个」）────
        print("\n[12b] AstrBot 筛选 / 翻页 / 数量提示")
        ok("分类统计是全量（跟搜索词无关）",
           sum(c["count"] for c in M.astrbot_search("")["cats"]) == 3,
           M.astrbot_search("")["cats"])
        dc = M.astrbot_search("", category="娱乐")
        ok("按分类筛选", dc["matched"] == 2, dc["matched"])
        ok("分类下拉带中文名", any(c["label"] == "娱乐" for c in dc["cats"]))
        p1 = M.astrbot_search("", limit=1, offset=0)
        p2 = M.astrbot_search("", limit=1, offset=1)
        ok("offset 翻页不重复", p1["items"][0]["id"] != p2["items"][0]["id"],
           [p1["items"][0]["id"], p2["items"][0]["id"]])
        ok("还有更多会说出来", p1["has_more"] is True and p1["shown"] == 1, p1["shown"])
        ok("一屏装得下时 has_more=False",
           M.astrbot_search("", limit=10, offset=0)["has_more"] is False)
        ok("limit 上限放到 300 而不是 60",
           len(M.astrbot_search("", limit=999)["items"]) == 3)

        # ── [13] 情绪标准返图（2026-10-26 主人：「让我能选定几张作为标准返图」）
        print("\n[13] 情绪标准返图：钉住 → 优先发 → 取消")
        T.bind(cfg=cfg, state_dir=os.path.join(tmp, "st"), base=tmp,
               send_image_file=lambda c, p, cap="": True)
        board = T.mood_board("fibi-memes", "无语")
        ok("面板给情绪列表", board["mood"] == "无语" and "开心" in board["moods"],
           board["moods"][:4])
        ok("面板有候选图", len(board["items"]) >= 1, len(board["items"]))
        ok("候选带缩略图地址",
           board["items"][0]["thumb"].startswith("/api/thumb?skill=fibi-memes"),
           board["items"][0]["thumb"][:40])
        bad = T.set_pins("fibi-memes", "无语", ["不存在的图.gif"])
        ok("钉不存在的图被拒", not bad[0], bad[1][:20])
        ok("不认识的技能被拒", not T.set_pins("petpet", "无语", [])[0])
        ok("不认识的情绪被拒", not T.set_pins("fibi-memes", "不存在", [])[0])
        target = [x["id"] for x in board["items"]][:2]
        ok("能钉 2 张", T.set_pins("fibi-memes", "无语", target)[0], target)
        bot.CFG["skills"]["settings"]["fibi-memes"]["pins"] = {"无语": target}
        cand, _ = T.fibi_candidates("无语", want=3, cid="", shuffle=True)
        ok("钉住的排最前", [x["id"] for x in cand[:2]] == target,
           [x["id"] for x in cand[:3]])
        T.fibi_send(cid=999, keyword="无语", count=1)
        cand2, _ = T.fibi_candidates("无语", want=3, cid=999, shuffle=True)
        ok("钉住的图不受「同会话不重发」限制",
           cand2[0]["id"] == target[0], cand2[0]["id"])
        before_th = sorted(os.listdir(pics))
        tp = T.thumb_path("fibi-memes", target[0])
        ok("缩略图能生成", bool(tp) and os.path.isfile(tp) and os.path.getsize(tp) > 0)
        ok("缩略图写缓存不写图库", sorted(os.listdir(pics)) == before_th,
           [x for x in sorted(os.listdir(pics)) if x not in before_th])
        ok("缩略图缓存复用", T.thumb_path("fibi-memes", target[0]) == tp)
        ok("没这图就没缩略图", T.thumb_path("fibi-memes", "不存在.gif") is None)
        board2 = T.mood_board("fibi-memes", "无语")
        ok("面板能看出哪张钉了", any(x["pinned"] for x in board2["items"]))
        ok("取消钉住", T.set_pins("fibi-memes", "无语", [])[0])
        bot.CFG["skills"]["settings"]["fibi-memes"].pop("pins", None)

        # ── [14] 图库：跟随网站更新 + 群里收图（2026-10-26 晚）───────────
        print("\n[14] 图库：跟随网站更新（只增不减）+ 群里收图")
        T.bind(cfg=cfg, state_dir=os.path.join(tmp, "st"), base=tmp,
               send_image_file=lambda c, p, cap="": True)
        blob = open(real[0], "rb").read()
        fake = {"memes": [
            {"id": 9001, "title": "新来的菲比", "url": "images/新来的菲比.gif", "isGif": True},
            {"id": 9002, "title": "早就有了", "url": "images/早就有了.gif", "isGif": True}]}
        shutil.copy2(real[0], os.path.join(pics, "早就有了.gif"))   # 本地已存在，不该重下
        real_http = T._fibi_http
        hits = []

        def fake_http(rel, timeout=30):
            hits.append(rel)
            if rel.endswith("memes.json"):
                return json.dumps(fake, ensure_ascii=False).encode("utf-8")
            return blob

        T._fibi_http = fake_http
        s_ok, s_msg = T.fibi_sync()
        ok("同步能下新图", s_ok and "新增 1" in s_msg, s_msg)
        ok("新图真的落盘", os.path.isfile(os.path.join(pics, "新来的菲比.gif")))
        ok("本地已有的不重复下", "早就有了.gif" not in hits, [h for h in hits if "gif" in h])
        ok("同步不删本地文件", os.path.isfile(os.path.join(pics, "无语菲比.gif")))
        n_before = len(os.listdir(pics))

        def dead_http(rel, timeout=30):
            raise RuntimeError("网络断了")

        T._fibi_http = dead_http
        d_ok, d_msg = T.fibi_sync()
        ok("拉不到索引就明说失败", not d_ok and "拉不到" in d_msg, d_msg[:30])
        ok("同步失败不动本地图", len(os.listdir(pics)) == n_before)
        T._fibi_http = real_http

        # 收图：名字不带「菲比」也要能进索引（collected 记账放行）
        g_ok, g_msg, g_name = T.fibi_add(os.path.join(pics, "新来的菲比.gif"), "群友发的表情")
        ok("收图成功", g_ok and os.path.isfile(os.path.join(pics, g_name)), g_msg[:36])
        ok("收的图不受 match 限制（能被索引到）",
           any(x["id"] == g_name for x in T._fibi_index(force=True)), g_name)
        g2_ok, _g2, g2_name = T.fibi_add(os.path.join(pics, "新来的菲比.gif"), "群友发的表情")
        ok("同名自动加序号", g2_ok and g2_name != g_name, [g_name, g2_name])
        g3_ok, _g3, _g3n = T.fibi_add(os.path.join(pics, "新来的菲比.gif"), "")
        ok("不给名字就自动命名", g3_ok and _g3n.startswith("收图_"), _g3n)
        txt = os.path.join(tmp, "not_img.txt")
        open(txt, "w").write("hello")
        ok("非图片被拒", not T.fibi_add(txt, "x")[0])
        tiny = os.path.join(tmp, "tiny.gif")
        open(tiny, "wb").write(b"GIF89a" + b"0" * 100)
        ok("太小的图被拒", not T.fibi_add(tiny, "y")[0])
        st = T.fibi_sync_status()
        ok("状态里带图源与收图数",
           st["site"].startswith("https://") and st["collected"] >= 3, st["collected"])

        # 群口令「收图」：只认管理员、没图给用法、别的话不拦
        grab_src = os.path.join(tmp, "grab_src.gif")
        shutil.copy2(real[0], grab_src)
        sent = []
        real_send, real_fetch = bot.send_group, bot._fetch_raw_image
        bot.send_group = lambda gid, text, *a, **k: sent.append(text)
        bot._fetch_raw_image = lambda d: grab_src
        ok("「收图 名字」能收",
           bot.handle_emoji_grab(1, "收图 口令收的图", True, [{"url": "x"}], 0) is True
           and any("收好了" in x for x in sent), (sent[-1][:34] if sent else ""))
        sent.clear()
        bot.handle_emoji_grab(1, "收图", False, [{"url": "x"}], 0)
        ok("非管理员不能收", bool(sent) and "管理员" in sent[-1], sent[-1][:20] if sent else "")
        sent.clear()
        bot.handle_emoji_grab(1, "收图 空的", True, [], 0)
        ok("没图时给用法", bool(sent) and "收哪张" in sent[-1], sent[-1][:22] if sent else "")
        ok("不是收图口令就不拦", bot.handle_emoji_grab(1, "这个图挺好笑的", True, [], 0) is False)
        bot.send_group, bot._fetch_raw_image = real_send, real_fetch
        T.CFG, T.STATE, T.BASE, T._SEND_IMAGE = real_bind

        print("\n[15] 梗百科：本地词库 / 卡片 / 预查注入 / 口令")
        import qbot_memewiki as MW
        # 词库用只读副本，卡片 / auto.json 全落临时目录：测试不写真实 state。
        wm_state = os.path.join(tmp, "state")
        os.makedirs(os.path.join(wm_state, "memewiki"), exist_ok=True)
        _memes_db = os.path.join(ROOT, "state", "memewiki", "memes.db")
        if not os.path.isfile(_memes_db):
            print("SKIP: state/memewiki/memes.db 未生成（梗百科词库需本地构建）")
            return 0
        shutil.copy2(_memes_db, os.path.join(wm_state, "memewiki", "memes.db"))
        MW.bind(base=ROOT, state_dir=wm_state, logger=None)
        st = MW.stats()
        ok("词库在、条数对得上", st["count"] >= 6000, "%d 条 / %s" % (st["count"], st["built_at"]))
        d, src = MW.lookup_data("大水理论", allow_online=False)
        ok("精确命中本地词库", src == "local" and d["title"] == "大水理论")
        ok("词条带用法和出处", bool(d["usage"]) and bool(d["origin"]))
        d2, src2 = MW.lookup_data("你是什么冠军", allow_online=False)
        ok("标题包含在句子里也能命中", src2 == "local" and d2["title"] == "你是什么冠军")
        d3, src3 = MW.lookup_data("完全没有这个词xxyy", allow_online=False)
        ok("没收录返回 none", src3 == "none" and d3 is None)
        ok("不联网时绝不发请求", MW.lookup_data("寿来", allow_online=False)[1] == "local")

        txt = MW.render_text(d, for_llm=False)
        ok("给群友看：来源在前", txt.index("【来源】") < txt.index("【用法】"))
        llm = MW.render_text(d, for_llm=True)
        ok("给模型看：怎么用在前", llm.index("【怎么用") < llm.index("【出处"))
        ok("正文带来源链接", "cnmeme.wiki/词条/" in llm)

        p = MW.render_card(d)
        ok("卡片画出来了", bool(p) and os.path.exists(p) and os.path.getsize(p) > 3000,
           (str(os.path.getsize(p)) + "B") if p and os.path.exists(p) else "无")
        kind, val = MW.answer("寿来")
        ok("「梗 X」默认回卡片", kind == "card" and os.path.exists(val))
        cfg["skills"]["settings"].setdefault("memewiki", {})["replyMode"] = "纯文字"
        ok("切成纯文字就回文字", MW.answer("寿来")[0] == "text")
        cfg["skills"]["settings"]["memewiki"]["replyMode"] = "图片卡片"

        # 预查闸门：只在「像在问梗」时才插话
        ok("明确问梗 → 注入词条", "【梗百科】大水理论" in MW.prelook("大水理论是什么梗"))
        ok("裸词命中 → 注入", "【梗百科】寿来" in MW.prelook("寿来"))
        ok("闲聊整句 → 不插话", MW.prelook("今天好累啊") == "")
        ok("客套话 → 不插话", MW.prelook("晚安") == "")
        cfg["skills"]["settings"]["memewiki"]["online"] = False   # 别让测试真去联网
        miss = MW.prelook("xxyy是什么梗")
        ok("明确问梗但没收录 → 提醒别硬编", "别硬编" in miss and "xxyy" in miss, miss[:24])
        ok("裸词没收录 → 静默（不刷屏）", MW.prelook("qwertyuiop") == "")
        cfg["skills"]["settings"]["memewiki"]["inject"] = False
        ok("关掉自动查梗就不注入", MW.prelook("大水理论是什么梗") == "")
        cfg["skills"]["settings"]["memewiki"]["inject"] = True
        cfg["skills"]["settings"]["memewiki"]["online"] = True

        # 擦边词条不查（跟人设里的拦截规矩一致）
        ok("擦边标签的词条拒答", MW.answer("黑丝麻将")[1].startswith("这个不查")
           or MW.lookup_data("黑丝麻将", allow_online=False)[1] == "blocked")

        # 工具入口（MCP 走这条路）
        okk, tt = T.call("memewiki", "lookup", term="大水理论")
        ok("技能工具 lookup 能用", okk and "大水理论" in tt, tt[:30])
        okk, tt = T.call("memewiki", "catalog", limit=5)
        ok("技能工具 catalog 能用", okk and "梗百科词库" in tt, tt[:30])
        ok("工具表里认得出 memewiki", T.has_native("memewiki"))
        ok("网页知道它有同步通道",
           bot.qbot_skill_tools.SYNC_KINDS.get("memewiki") == "memewiki")

        # 群口令「梗 X」
        sent = []
        real_send, real_segs = bot.send_group, bot.send_segments
        bot.send_group = lambda gid, text, *a, **k: sent.append(text)
        bot.send_segments = lambda gid, segs, *a, **k: True
        ok("「梗 大水理论」发卡片", bot.handle_memewiki(1, "梗 大水理论") is True)
        sent.clear()
        bot.handle_memewiki(1, "梗 完全没有的词xxyy")
        ok("查不到直说没收录", bool(sent) and "没有收录" in sent[-1], sent[-1][:24] if sent else "")
        sent.clear()
        bot.handle_memewiki(1, "梗")
        ok("只说「梗」给用法", bool(sent) and "用法" in sent[-1], sent[-1][:20] if sent else "")
        ok("「梗概很好」不误伤", bot.handle_memewiki(1, "梗概很好") is False)
        ok("「梗图」不误伤", bot.handle_memewiki(1, "来张梗图") is False)
        bot.send_group, bot.send_segments = real_send, real_segs

        print("\n[16] 梗百科：每天自动更新（到点才跑、一天只跑一次）")
        real_start = MW.sync_start
        starts = []
        MW.sync_start = lambda: (starts.append(1), {"ok": True, "running": True})[1]
        auto_p = os.path.join(wm_state, "memewiki", "auto.json")
        auto_bak = open(auto_p, "rb").read() if os.path.exists(auto_p) else None
        try:
            cfg["skills"]["settings"]["memewiki"]["autoSync"] = True
            cfg["skills"]["settings"]["memewiki"]["autoSyncHour"] = 4
            a = MW.auto_status()
            ok("状态里带自动更新开关与时刻", a["enabled"] is True and a["hour"] == 4, a)
            wrong = time.mktime((2026, 10, 27, 15, 0, 0, 0, 0, -1))
            ok("没到点不跑", MW.auto_tick(now=wrong) is False and not starts)
            right = time.mktime((2026, 10, 27, 4, 0, 30, 0, 0, -1))
            ok("到点就跑", MW.auto_tick(now=right) is True and len(starts) == 1)
            ok("同一天不重复跑", MW.auto_tick(now=right + 900) is False and len(starts) == 1)
            nxt = time.mktime((2026, 10, 28, 4, 1, 0, 0, 0, -1))
            ok("第二天再跑", MW.auto_tick(now=nxt) is True and len(starts) == 2)
            cfg["skills"]["settings"]["memewiki"]["autoSync"] = False
            later = time.mktime((2026, 10, 29, 4, 1, 0, 0, 0, -1))
            ok("关掉就不跑", MW.auto_tick(now=later) is False and len(starts) == 2)
            cfg["skills"]["settings"]["memewiki"]["autoSync"] = True
            ok("自动更新进了状态接口", "auto" in MW.sync_status()
               and MW.sync_status()["auto"]["hour"] == 4)
        finally:
            MW.sync_start = real_start
            if auto_bak is None:
                if os.path.exists(auto_p):
                    os.remove(auto_p)
            else:
                open(auto_p, "wb").write(auto_bak)

    finally:
        bot.KNOBS[:] = real_knobs
        bot.save_config = real_save
        for k, v in real_cfg.items():
            if v is not None:
                bot.CFG[k] = v
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s" % ("─" * 56))
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：\n  - " + "\n  - ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
