# -*- coding: utf-8 -*-
"""图片混淆 / 解混淆 回归（2026-10-06 新增，主人群里提的需求）。

三段：
  A. scramble.py 核心算法 —— 与网页版 pic-scramble 的 JS 曲线**逐点一致**（node 跑原版做基准）
     + 各种尺寸往返还原 + 性能预算 + 磁盘缓存 + GIF/RGBA/坏文件 + 无 numpy 兜底
  B. bot 接线 —— 口令识别（含**误触发反例**）/ 私发目标 / 旋钮注册 / config 零污染
  C. 端到端 —— ★群里「解混淆」必须私发给本人★（主人定的行为，改这块前先看 C 段）

跑法：PYTHONIOENCODING=utf-8 python tests/_tscramble.py
⚠️ 不发真消息：send_segments / send_group 全打了桩。
基准曲线 JSON 在 %TEMP%/refcurve.json（node 跑原版 JS 生成，
   见 tests/_tscramble_ref.js）。文件不在就跳过那一项 —— 别手工造，会骗自己。
"""

import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = os.environ.get("TEMP", ".")
REF = os.path.join(TMP, "refcurve.json")

# ============================== A. 核心算法 ==============================
def run_A():
    import numpy as np
    from PIL import Image, ImageDraw
    from qbot import scramble
    # 2026-11-05 审计 U2：产物与曲线缓存全部落临时目录，跑完即删——
    # 原版写死 state/scramble/_t 并清空生产曲线缓存，SAFE 里不该碰生产 state。
    OUT = tempfile.mkdtemp(prefix="tscramble_a_")
    CACHE = Path(OUT) / "curve"
    scramble.CACHE_DIR = CACHE
    os.makedirs(OUT, exist_ok=True)
    fails = []

    def chk(cond, msg):
        print(("OK  " if cond else "FAIL") + "  " + msg)
        if not cond: fails.append(msg)

    # ---------- 1. 广义希尔伯特曲线双射性与点位覆盖自检 ----------
    if os.path.exists(REF):
        try:
            ref = json.load(open(REF, encoding="utf-8"))
            allok = True
            for k, coords in ref.items():
                w, h = map(int, k.split("x"))
                idx = scramble.curve(w, h)
                got = [[int(idx[i]) % w, int(idx[i]) // w] for i in range(len(coords))]
                if got != coords:
                    allok = False; print("  不一致:", k)
            chk(allok, "曲线与原网页 JS 逐点一致（%d 种尺寸）" % len(ref))
        except Exception as e:
            chk(False, "读取外部参考曲线失败: %s" % e)
    else:
        # 外部临时文件不存在时：自检空间填充曲线双射性（单射+满射：零缺失零重复）与网格覆盖
        test_sizes = [(1, 1), (2, 3), (8, 8), (17, 23), (32, 24), (64, 64), (128, 96)]
        bijection_ok = True
        for w, h in test_sizes:
            idx = scramble.curve(w, h)
            if len(idx) != w * h or len(set(idx)) != w * h:
                bijection_ok = False; break
            if min(idx) != 0 or max(idx) != w * h - 1:
                bijection_ok = False; break
        chk(bijection_ok, "曲线空间填充双射性与全域覆盖自检（%d 种奇偶/非对称尺寸）" % len(test_sizes))

    # ---------- 2. 图像往返 ----------
    def make(w,h,name):
        im = Image.new("RGB",(w,h),(30,40,60)); d=ImageDraw.Draw(im)
        for i in range(0,w,max(1,w//12)): d.line([(i,0),(i,h)],fill=(255,120-i%200,40),width=2)
        for j in range(0,h,max(1,h//9)): d.line([(0,j),(w,j)],fill=(40,255,j%200),width=2)
        d.ellipse([w*0.2,h*0.15,w*0.8,h*0.85],fill=(240,230,90))
        d.rectangle([w*0.05,h*0.05,w*0.35,h*0.35],fill=(200,30,60))
        p=os.path.join(OUT,name); im.save(p,"PNG"); return p

    for w,h in [(64,64),(128,96),(96,128),(200,150),(37,53),(1,1),(300,200)]:
        src = make(w,h,"o_%dx%d.png"%(w,h))
        base = scramble.load_rgb(src).copy()
        p1,sz,dt1 = scramble.scramble(src, OUT)
        scr = scramble.load_rgb(p1)
        p2,sz2,dt2 = scramble.descamble(p1, OUT)
        back = scramble.load_rgb(p2)
        err = float(np.abs(back.astype(int)-base.astype(int)).mean())
        changed = not np.array_equal(scr, base)
        chk(changed and err < 6.0 and sz==sz2==(w,h),
            "往返 %dx%d 混淆%.2fs 解混淆%.2fs 误差%.2f（图已改变=%s）" % (w,h,dt1,dt2,err,changed))

    # ---------- 3. 指定尺寸解混淆（平台缩放兜底）----------
    src = make(160,120,"o_big.png")
    p1,_,_ = scramble.scramble(src, OUT, max_side=10**6, max_pixels=10**9)
    small,_ ,_= scramble.descamble(p1, OUT, size=(160,120))
    err = float(np.abs(scramble.load_rgb(small).astype(int)-scramble.load_rgb(src).astype(int)).mean())
    chk(err<6.0, "指定原始尺寸解混淆 误差%.2f" % err)
    try:
        scramble.descamble(p1, OUT, size=(100,100)); chk(False,"比例不符应拒绝")
    except scramble.ScrambleError as e:
        chk(True, "比例不符已拒绝")

    # ---------- 4. 性能 + 磁盘缓存 ----------
    scramble._CURVE_CACHE.clear(); shutil.rmtree(CACHE, ignore_errors=True)
    src = make(1536,1024,"perf.png")
    t=time.time(); p,sz,dt = scramble.scramble(src, OUT, max_side=1536, max_pixels=10**9)
    print("   1536x1024 首次(含现算曲线) %.2fs" % (time.time()-t))
    chk(dt < 4.0, "1536x1024 混淆 %.2fs（曲线现算，预算 4s）" % dt)
    chk(os.path.isfile(CACHE/"c_1536x1024.npy"), "曲线已落盘缓存")
    scramble._CURVE_CACHE.clear()
    t=time.time(); p,sz,dt2 = scramble.scramble(src, OUT, max_side=1536, max_pixels=10**9)
    chk(dt2 < 1.0, "1536x1024 二次(读盘缓存) %.2fs" % dt2)
    t=time.time(); p3,_,dt3 = scramble.descamble(p, OUT)
    chk(dt3 < 1.5, "1536x1024 解混淆 %.2fs" % dt3)

    # ---------- 5. 限幅 ----------
    big = make(3000,2000,"big.png")
    p,sz,dt = scramble.scramble(big, OUT, max_side=1024, max_pixels=10**9)
    chk(sz==(1024,682), "3000x2000 限幅到 %s" % str(sz))
    p,sz,dt = scramble.scramble(big, OUT, max_side=4096, max_pixels=1_000_000)
    chk(sz[0]*sz[1] <= 1_000_000, "max_pixels 兜底 %s (%d 像素)" % (str(sz), sz[0]*sz[1]))

    # ---------- 6. GIF / RGBA / 坏文件 ----------
    g = os.path.join(OUT,"a.gif"); Image.new("RGB",(40,40),(200,50,50)).save(g,"GIF")
    p,sz,_ = scramble.scramble(g, OUT); chk(sz==(40,40), "GIF 取首帧 %s" % str(sz))
    rgba = os.path.join(OUT,"t.png"); Image.new("RGBA",(50,50),(255,0,0,128)).save(rgba,"PNG")
    a = scramble.load_rgb(rgba); chk(a.shape==(50,50,3), "RGBA 铺白底 -> %s" % (a.shape,))
    p,_,_ = scramble.scramble(rgba, OUT); chk(os.path.isfile(p), "RGBA 混淆成功")
    bad = os.path.join(OUT,"bad.png"); open(bad,"wb").write(b"not an image")
    try:
        scramble.scramble(bad, OUT); chk(False,"坏文件应报错")
    except scramble.ScrambleError as e:
        chk(True, "坏文件已报错: %s" % str(e)[:40])

    # ---------- 7. 无 numpy 兜底 ----------
    try:
        scramble.scramble(bad, OUT)
    except scramble.ScrambleError:
        pass
    _npsave = scramble._np
    try:
        scramble._np = None
        scramble._CURVE_CACHE.clear()
        src2 = make(24,18,"tiny.png")
        base2 = np.asarray(scramble.load_rgb(src2)).astype(int)   # _np=None 时 load_rgb 返回 PIL
        p1,_,_ = scramble.scramble(src2, OUT)
        p2,_,_ = scramble.descamble(p1, OUT)
        back2 = np.asarray(scramble.load_rgb(p2)).astype(int)
        err2 = float(np.abs(back2-base2).mean())
        chk(err2 < 6.0, "无 numpy 兜底往返 误差%.2f" % err2)
    finally:
        scramble._np = _npsave

    # ---------- 8. parse_size ----------
    ps = scramble.parse_size
    chk(ps("1536x1536")==(1536,1536) and ps("1536×1536")==(1536,1536)
        and ps("1536,1536")==(1536,1536) and ps("尺寸 800 x 600")==(800,600)
        and ps("没有尺寸") is None, "parse_size 各种写法")

    # ---------- 9. offset 取整 ----------
    ok = all(scramble.offset_for(w,h) == int(math.floor(((math.sqrt(5)-1)/2)*w*h + 0.5)) % max(1,w*h)
             for w,h in [(1024,1024),(1536,1024),(1920,1080),(37,53)])
    chk(ok, "offset 与 JS Math.round 一致")

    # ---------- 10. GC ----------
    for i in range(6): scramble.scramble(make(60,60,"g%d.png"%i), OUT, keep=3)
    left = len([p for p in os.listdir(OUT) if p.startswith(("scr_","dec_"))])
    chk(left <= 3, "产物 GC 保留 %d 个（keep=3）" % left)

    shutil.rmtree(OUT, ignore_errors=True)      # 临时产物跑完即删（U2；CACHE_DIR 留指向，
    return fails                                #  后续段按需自动重建，生产曲线缓存零接触）

# ============================== B. bot 接线 ==============================
def run_B():
    import bot
    fails = []

    def chk(c, m):
        print(("OK  " if c else "FAIL") + "  " + m)
        if not c: fails.append(m)

    # ---- 1. 口令识别 ----
    hit_s = ["混淆", "混淆一下", "图片混淆", "打码", "打码图", "打乱", "混淆 这张", "请混淆", "混淆一张",
             "混淆私发", "帮我混淆", "打码私发", "混淆，私发"]
    hit_d = ["解混淆", "解混淆 1536x1024", "还原", "复原", "解密图", "图片解混淆", "反混淆", "解马赛克",
             # ★ 2026-10-06 主人实测漏掉的那批（他打成「解混淆私发」结果不认，被迫重发图）★
             "解混淆私发", "解混淆 私发", "解混淆，私发", "解混淆，私发给我", "私发解混淆",
             "解混淆发我", "解混淆 1536x1024 私发", "帮我解混淆私发", "还原私发", "解混淆别发群"]
    miss  = ["混淆度很高", "这段代码需要解密思路", "还原度", "回忆一下", "帮我看看这张图",
             "复原了没", "打瞌睡", "解密一下这个谜题吧", "我私发给你了", "他私发给我了"]
    for t in hit_s: chk(bot.wants_scramble(t) and not bot.wants_descamble(t), "混淆口令认: %r" % t)
    for t in hit_d: chk(bot.wants_descamble(t), "解混淆口令认: %r" % t)
    for t in miss:  chk(not (bot.wants_scramble(t) or bot.wants_descamble(t)), "不该触发: %r" % t)
    # 解混淆必须优先于混淆（"图片解混淆" 含"混淆"）
    chk(bot.wants_descamble("图片解混淆") and not bot.wants_scramble("图片解混淆"),
        "「图片解混淆」优先判为解混淆")

    # ---- 1b. 转发记录取图（「别人再看你，非要别人再发一遍」就是这里漏的）----
    _IMG = {"type": "image", "data": {"url": "https://x/a.jpg", "file": "a.jpg"}}
    _fwd1 = [{"type": "forward", "data": {"id": "1", "content": [
        {"message": [{"type": "text", "data": {"text": "看这个"}}, _IMG]}]}}]
    chk(len(bot.forward_images(_fwd1)) == 1, "转发消息里的图能抠出来（以前整条漏掉）")
    _fwd2 = [{"type": "forward", "data": {"id": "1", "content": [
        {"message": [{"type": "forward", "data": {"id": "2", "content": [{"message": [_IMG]}]}}]}]}}]
    chk(len(bot.forward_images(_fwd2)) == 1, "嵌套转发也能钻到图")
    chk(bot.forward_images([{"type": "text", "data": {"text": "hi"}}]) == [], "没转发段时返回空")
    # 素材记忆：转发消息也要被记住（「转发图 + 另发一句口令」靠回捞）
    _src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "bot.py"), encoding="utf-8").read()
    chk("_fwd_imgs = forward_images(data.get(\"message\"))" in _src,
        "转发图会进素材记忆（回捞能命中，不用重发）")

    # ---- 1c. 私聊假失败复核（NapCat 对群临时会话常报 failed 但已投递）----
    _real_api = bot.api
    try:
        bot.api = lambda action, **kw: {"status": "ok", "data": [
            {"peerUin": "555", "msgTime": str(int(time.time())),
             "lastestMsg": {"post_type": "message_sent", "msgId": "7693439412804423725"}}]}
        chk(bot.verify_private_sent(555, int(time.time())) is True,
            "假失败复核：最近联系人里有刚发的 msgId ⇒ 判成功")
        bot.api = lambda action, **kw: {"status": "ok", "data": [
            {"peerUin": "555", "msgTime": str(int(time.time()) - 99999),
             "lastestMsg": {"post_type": "message_sent", "msgId": "1"}}]}
        chk(bot.verify_private_sent(555, int(time.time())) is False,
            "假失败复核：msgTime 很旧 ⇒ 判真失败")
        bot.api = lambda action, **kw: {"status": "ok", "data": []}
        chk(bot.verify_private_sent(555, int(time.time())) is None,
            "假失败复核：查不到会话 ⇒ 返回 None（不乱判）")

        # ★ 集成：send_segments 收到 failed 时必须自动复核，别把成功的私发当失败 ★
        real_send = bot._api_send
        try:
            bot._api_send = lambda cid, msg, hint=None: {
                "status": "failed", "retcode": 200,
                "message": "EventChecker Failed: NTEvent .../sendMsg"}
            bot.api = lambda action, **kw: {"status": "ok", "data": [
                {"peerUin": "555", "msgTime": str(int(time.time())),
                 "lastestMsg": {"post_type": "message_sent", "msgId": "7693439412804423725"}}]}
            got = bot.send_segments(bot.priv_key(555),
                                    [{"type": "text", "data": {"text": "x"}}], group_hint=123450001)
            chk(got is True, "★send_segments：NapCat 报 failed 但复核确认已投递 ⇒ 返回 True★")
            # 复核不通过时仍要如实返回 False
            bot.api = lambda action, **kw: {"status": "ok", "data": []}
            got2 = bot.send_segments(bot.priv_key(555),
                                     [{"type": "text", "data": {"text": "x"}}], group_hint=123450001)
            chk(got2 is False, "send_segments：复核也查不到 ⇒ 如实返回 False")
        finally:
            bot._api_send = real_send
    finally:
        bot.api = _real_api

    # ---- 2. 私发目标 ----
    chk(bot.priv_key(123450002) == -123450002, "priv_key 是负的")
    chk(bot.is_priv(bot.priv_key(123)), "priv_key 后 is_priv 为真")
    chk(not bot.is_priv(123450001), "群号不是 priv")

    # ---- 3. 素材回捞不炸（没有素材时返回 None）----
    try:
        r = bot._scr_pick_image([], 0, 999999, 123450001)
        chk(r is None, "无素材时返回 None（而不是抛异常）")
    except Exception as e:
        chk(False, "无素材时抛异常: %s" % e)

    # ---- 4. 旋钮注册 ----
    cfg_items = bot.knob_items()
    keys = {i["key"] for i in cfg_items}
    for k in ["scramble.enabled","scramble.members_allowed","scramble.quality",
              "scramble.max_side","scramble.max_pixels","scramble.keep"]:
        chk(k in keys, "旋钮已注册: %s" % k)
    grp = {i["group"] for i in cfg_items}
    chk("图库" in grp, "归到「图库」分区")
    found = [i for i in cfg_items if i["key"] == "scramble.enabled"][0]
    chk(found["perm"] == "admin" and found["apply"] == "hot", "权限 admin + hot 生效")
    chk("没有密钥" in found["desc"], "desc 里说明了没有密钥")

    # ---- 5. 真 config 能读到值 ----
    sc = bot._scr_cfg()
    chk(sc.get("enabled") is True, "真 config 读到 enabled=%s" % sc.get("enabled"))
    chk(int(sc.get("max_side")) == 1536, "max_side=%s" % sc.get("max_side"))
    chk(int(sc.get("max_pixels")) == 4000000, "max_pixels=%s" % sc.get("max_pixels"))
    chk(bot.SCR_DIR and str(bot.SCR_DIR).endswith("scramble"), "产物目录 %s" % bot.SCR_DIR)

    # ---- 6. scramble 段结构正确 ----
    # ⚠️ 这里原先是「拿历史备份 config.json.bak-1006-scramble 对比，断言 config
    #    其余部分零改动」—— 那是**一次性验证**（证明我当初没污染别的配置），
    #    **不能当长期回归**：config 是活的，主人随时会改档位/旋钮，
    #    这条必然过期飘红（2026-10-06 当晚就踩到了）。
    #    换成有长期价值的结构检查（读 bot.CFG——生产代码读的就是它，只读不写）。
    _sc = (bot.CFG.get("scramble") or {})
    chk(isinstance(_sc, dict) and _sc.get("enabled") is True
        and int(_sc.get("max_side") or 0) > 0 and int(_sc.get("max_pixels") or 0) > 0
        and 60 <= int(_sc.get("quality") or 0) <= 100 and "keep" in _sc,
        "config 里 scramble 段结构齐全（%s）" % sorted(_sc.keys())[:6])

    # ---- 7. 模块可用 ----
    chk(bot.scramble is not None, "scramble 模块已加载（PIL+numpy 可用）")
    chk(hasattr(bot, "handle_scramble_cmd"), "handle_scramble_cmd 已挂上")

    # ---- 8. help 文案 ----
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bot.py"), encoding="utf-8").read()
    chk("「解混淆」我私发还原结果给你" in src, "/help 已补混淆说明")


    return fails

# ============================== C. 端到端 ==============================
def run_C():
    import numpy as np
    from PIL import Image, ImageDraw
    import bot
    # 2026-11-05 审计 U2：端到端产物落临时目录，跑完即删（原版写死 state/scramble/_e2e）。
    OUT = tempfile.mkdtemp(prefix="tscramble_c_")
    os.makedirs(OUT, exist_ok=True)

    SENT = []
    HINTS = []          # 记录每次私发带的群号（群临时会话）
    def fake_send_segments(cid, segs, group_hint=None):
        SENT.append((cid, segs)); HINTS.append(group_hint); return True
    def fake_send_group(cid, t, group_hint=None):
        SENT.append((cid, t)); HINTS.append(group_hint); return True
    bot.send_segments = fake_send_segments
    bot.send_group = fake_send_group

    fails=[]
    def chk(c,m):
        print(("OK  " if c else "FAIL")+"  "+m)
        if not c: fails.append(m)

    # 真图素材
    def make(w,h,name):
        im = Image.new("RGB",(w,h),(20,60,120)); d=ImageDraw.Draw(im)
        for i in range(0,w,max(1,w//8)): d.line([(i,0),(i,h)],fill=(255,i%256,0),width=3)
        d.ellipse([w*0.15,h*0.15,w*0.85,h*0.85], fill=(250,240,30))
        d.text((10,10), "TEST", fill=(255,255,255))
        p=os.path.join(OUT,name); im.save(p,"PNG"); return p

    real_pick = bot._scr_pick_image
    CURRENT = [None]
    def fake_pick(imgs,qmid,uid=None,gid=None,raw_msg=None):
        return CURRENT[0]
    bot._scr_pick_image = fake_pick

    # ---- 1. 群里混淆：应发到群会话（正数群号）----
    SRC = make(400,300,"e1.png")
    CURRENT[0] = SRC
    SENT.clear()
    done = bot.handle_scramble_cmd(123450001, "混淆", False, [{}], 0, uid=555)
    tgt = [c for c,_ in SENT]
    img_seg = [s for _,segs in SENT for s in (segs if isinstance(segs,list) else []) if s.get("type")=="image"]
    chk(done and 123450001 in tgt, "群聊混淆发到群 %s" % tgt)
    chk(bool(img_seg), "群聊混淆带上了图片段")
    chk("解混淆" in json.dumps([s for _,segs in SENT for s in (segs if isinstance(segs,list) else [])], ensure_ascii=False),
        "群里提示了怎么还原")
    SCR_PATH = img_seg[0]["data"]["file"] if img_seg else None
    chk(SCR_PATH and os.path.isfile(SCR_PATH), "混淆产物落盘 %s" % (os.path.basename(SCR_PATH) if SCR_PATH else None))

    # ---- 2. 群里解混淆：★必须私发给本人★（喂第 1 步产出的混淆图）----
    CURRENT[0] = SCR_PATH
    SENT.clear(); HINTS.clear()
    done = bot.handle_scramble_cmd(123450001, "解混淆", False, [{}], 0, uid=555)
    tgt = sorted({c for c,_ in SENT})
    priv_img = [s for c,segs in SENT for s in (segs if isinstance(segs,list) else []) if s.get("type")=="image" and c < 0]
    chk(-555 in tgt, "★群里解混淆私发给本人（-555）★ 实际目标 %s" % tgt)
    chk(123450001 in tgt, "群里也回了一句确认")
    chk(bool(priv_img), "私发通道里确实带了图")
    chk(not [s for c,segs in SENT for s in (segs if isinstance(segs,list) else []) if s.get("type")=="image" and c == 123450001],
        "★图没有发到群里★")
    # ★★ 群临时会话：私发必须带群号，否则非好友发不出去 ★★
    chk(123450001 in HINTS, "★私发带了 group_hint=群号（群临时会话，不用加好友）★ 实际 %s" % HINTS)
    DEC_PATH = priv_img[0]["data"]["file"] if priv_img else None

    # ---- 3. 私聊会话里解混淆：直接发私聊 ----
    SENT.clear()
    done = bot.handle_scramble_cmd(bot.priv_key(555), "解混淆", False, [{}], 0, uid=555)
    tgt = [c for c,_ in SENT]
    chk(bot.priv_key(555) in tgt, "私聊会话直接发私聊 %s" % tgt)

    # ---- 4. 群里解混淆 + 指定尺寸 ----
    SENT.clear()
    CURRENT[0] = SCR_PATH
    bot.handle_scramble_cmd(123450001, "解混淆 400x300", False, [{}], 0, uid=555)
    t = json.dumps([s for c,segs in SENT for s in (segs if isinstance(segs,list) else [])], ensure_ascii=False)
    chk("400×300" in t, "指定尺寸被采纳并回显")

    # ---- 5. 没图时的提示 ----
    bot._scr_pick_image = lambda *a, **k: None
    SENT.clear()
    done = bot.handle_scramble_cmd(123450001, "解混淆", False, [], 0, uid=555)
    txt = json.dumps([s for _,s in SENT], ensure_ascii=False)
    chk(done and "先把要" in txt, "无图时给出明确提示")
    bot._scr_pick_image = real_pick

    # ---- 6. 开关关闭 ----
    cfg = bot.CFG["scramble"]; old = cfg["enabled"]
    cfg["enabled"] = False
    SENT.clear(); done = bot.handle_scramble_cmd(123450001, "混淆", False, [{}], 0, uid=555)
    chk(done and "关的" in json.dumps([s for _,s in SENT], ensure_ascii=False), "enabled=False 时拒答")
    cfg["enabled"] = old

    # ---- 7. 群友禁用 ----
    cfg["members_allowed"] = False
    SENT.clear(); done = bot.handle_scramble_cmd(123450001, "混淆", False, [{}], 0, uid=555)
    chk(done and "管理员" in json.dumps([s for _,s in SENT], ensure_ascii=False), "members_allowed=False 时群友被拒")
    cfg["members_allowed"] = True

    # ---- 8. 画质/产物真还原（端到端出图验像素）----
    if SCR_PATH and DEC_PATH:
        a = np.asarray(Image.open(SCR_PATH).convert("RGB")).astype(int)
        b = np.asarray(Image.open(DEC_PATH).convert("RGB")).astype(int)
        o = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
        chk(a.shape == o.shape, "混淆图尺寸不变 %s" % (a.shape,))
        chk(not np.array_equal(a, o), "混淆图内容确实变了")
        err = float(np.abs(b-o).mean())
        chk(err < 8.0, "端到端解混淆误差 %.2f" % err)
        print("   混淆图 %d KB" % (os.path.getsize(SCR_PATH)//1024))

    # ---- 9. 坏图不炸群 ----
    BROKEN = os.path.join(OUT,"broken.png")
    open(BROKEN,"wb").write(b"junk")
    CURRENT[0] = BROKEN
    bot._scr_pick_image = fake_pick   # 第 5 节把 pick 换回了真实现，这里换回来
    SENT.clear(); done = bot.handle_scramble_cmd(123450001, "混淆", False, [{}], 0, uid=555)
    alltext = json.dumps([s for _,s in SENT], ensure_ascii=False)
    chk(done and "失败" in alltext, "坏图走失败分支而非抛异常")

    # ---- 10. uid 缺失不崩（解混淆私发路径）----
    CURRENT[0] = make(50,50,"e10.png")
    SENT.clear()
    try:
        done = bot.handle_scramble_cmd(123450001, "解混淆", False, [{}], 0, uid=None)
        chk(done, "uid=None 时不崩（回落发群）")
    except Exception as e:
        chk(False, "uid=None 时崩了: %s" % e)

    shutil.rmtree(OUT, ignore_errors=True)      # 临时产物跑完即删（U2）
    return fails

if __name__ == "__main__":
    allf = []
    for name, fn in (("A 核心算法", run_A), ("B bot 接线", run_B), ("C 端到端", run_C)):
        print("\n########## %s ##########" % name)
        f = fn()
        print("---- %s：失败 %d 项 ----" % (name, len(f)))
        allf += f
    print("\n================ 总失败 %d 项 ================" % len(allf))
    for x in allf:
        print(" -", x)
    sys.exit(1 if allf else 0)