# -*- coding: utf-8 -*-
"""捏脸表情包（petpet）—— 离线自测（2026-10-23）。

覆盖：清单加载 / 模板表自洽 / 口令识别（正例 + 反例）/ 渲染出图 / 冷却与额度 /
技能关闭 / 素材缺失降级。**全程不联网**：头像用 Pillow 现画一张假的，
走 `image_path` 那条路（跟群里「发图 + 口令」完全同一条代码路径）。

用法：python tests/_tpetpet.py
"""
import hashlib
import io
import json
import os as _os
import shutil
import sys as _sys
import tempfile
import time as _t
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

from qbot import qbot_skills as S          # noqa: E402
from qbot import qbot_skill_tools as T     # noqa: E402
from qbot import qbot_petpet as P          # noqa: E402
import bot                       # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


# ── 沙箱：状态目录换到临时目录，别把真额度写脏 ──────────────────────────
_tmp = Path(tempfile.mkdtemp(prefix="petpet_test_"))
P.OUTDIR = _tmp / "out"          # 渲染产物也落临时目录：别把 cache/petpet 的 120 个上限挤爆
T.bind(cfg=bot.CFG, state_dir=str(_tmp), base=_ROOT,
       send_image_file=lambda cid, path, cap="": _SENT.append((cid, str(path), cap)) or True)
_SENT = []
bot.CFG.setdefault("skills", {}).setdefault("en", {})["petpet"] = True
# 不依赖生产配置：主人可能随时在网页把「捏脸表情包」关掉；这项测试要在临时态自作主张开。
bot.CFG.setdefault("skills", {}).setdefault("settings", {}).setdefault(
    "petpet", {})["enabled"] = True
S.reload()

print("== 清单 ==")
sk = S.get("petpet")
chk("技能包装上了", sk is not None and sk["id"] == "petpet")
chk("没有加载错误", sk and not sk.get("err"), sk and sk.get("err"))
chk("提示词有 1 段", sk and len(sk["sections"]) == 1)
chk("参数 7 项", sk and len(sk["schema"]) == 7, sk and sorted(sk["schema"]))
chk("多了两个同步开关", sk and {"autoSync", "autoSyncHour"} <= set(sk["schema"]),
    sk and sorted(sk["schema"]))
chk("autoSyncHour 是 0~23", sk and sk["schema"]["autoSyncHour"].get("min") == 0
    and sk["schema"]["autoSyncHour"].get("max") == 23,
    sk and sk["schema"]["autoSyncHour"])
chk("index.js 工具名嗅探到", sk and "petpet_send" in sk["tools"], sk and sk["tools"])
chk("本机原生工具已接", T.has_native("petpet") and "send" in T.NATIVE["petpet"])
chk("提示词里写明「本机已接」", "捏脸表情包" in S.prompt_block() and "本机已接" in S.prompt_block())

print("== 模板表自洽 ==")
bad = []
for tid, spec in P.TEMPLATES.items():
    if not spec.get("words") or not spec.get("name"):
        bad.append((tid, "缺 words/name"))
    n = spec.get("frames")
    # 上游允许 locs 比帧数少（bite：只有前 6 帧贴头像，7~16 帧是纯动画），也允许比帧数多
    # （twist：5 帧 6 个位置）。只查「有没有」和「越界时能不能安全跳过」。
    if spec["kind"] == "under" and not spec.get("multi") and not spec.get("loc_by_index"):
        if not spec.get("locs"):
            bad.append((tid, "没有 locs"))
    if spec.get("need_sender") and len(spec.get("sender_locs") or []) < n:
        bad.append((tid, "sender_locs 不够"))
    if len(spec.get("seq") or []) > 0:
        for i in spec["seq"]:
            if i >= n:
                bad.append((tid, "seq 越界 %d" % i))
chk("每个模板参数齐全", not bad, bad)
chk("模板数量 >= 13", len(P.TEMPLATES) >= 13, len(P.TEMPLATES))
chk("bite 后半段不贴头像（上游设计：纯动画）",
    P._loc_of(P.TEMPLATES["bite"], 0) is not None
    and P._loc_of(P.TEMPLATES["bite"], 10) is None)
chk("pat 第 3 张图用第二个位置",
    P._loc_of(P.TEMPLATES["pat"], 2) == P.TEMPLATES["pat"]["locs"][1]
    and P._loc_of(P.TEMPLATES["pat"], 5) == P.TEMPLATES["pat"]["locs"][0])

print("== 口令识别 ==")
cases = [("摸摸头", "petpet"), ("揉一揉", "rub"), ("咬一口", "bite"), ("敲一敲", "knock"),
         ("拍一拍", "pat"), ("揍一拳", "punch"), ("滚一圈", "roll"), ("扭一扭", "twist"),
         ("扔出去", "throw_gif"), ("猫猫揉", "capoo_rub"), ("支持你", "support"),
         ("捶", "pound"), ("撞", "thump")]
for text, want in cases:
    got, word = P.resolve(text)
    chk("「%s」→ %s" % (text, want), got == want, got)
chk("不认识的句子不认", P.resolve("今天天气不错")[0] is None)
chk("长句里带动词不认（靠 startswith 兜）", P.resolve("我咬了一口苹果")[0] == "bite")  # resolve 只管词
chk("模板 id 也能当口令", P.resolve("pat")[0] == "pat")

print("== 渲染（本地假头像，不联网）==")
face = Image.new("RGBA", (640, 640), (250, 200, 60, 255))
_d = ImageDraw.Draw(face)
_d.ellipse((150, 120, 490, 460), fill=(60, 120, 220, 255))
_d.ellipse((250, 250, 300, 300), fill=(20, 20, 20, 255))
_d.ellipse((340, 250, 390, 300), fill=(20, 20, 20, 255))
_d.arc((270, 320, 370, 400), 20, 160, fill=(30, 30, 30, 255), width=8)
_face_path = _tmp / "face.png"
face.save(_face_path)

ready = [x["id"] for x in P.list_templates()]
chk("素材已就绪的模板 >= 5 个", len(ready) >= 5, ready)
for tid in ready:
    try:
        path, name = P.make_for(tid, "10001", image_path=str(_face_path))
        im = Image.open(path)
        frames = getattr(im, "n_frames", 1)
        size = path.stat().st_size
        good = path.exists() and frames >= 1 and size > 2000
        chk("%s（%s）出图 %d 帧 %.0fKB" % (tid, name, frames, size / 1024), good)
    except Exception as e:  # noqa: BLE001
        chk("%s 出图" % tid, False, "%s: %s" % (type(e).__name__, e))
missing = [t for t in P.TEMPLATES if t not in ready]
if missing:
    print("  （素材还没下：%s）" % "、".join(missing))

# 回归：rub 的帧是**不透明白底**，头像必须盖在上面。
# 第一版把 rub 当成 under 处理，结果头像被白底整块糊掉，图里只剩两个人形线稿。
if "rub" in ready:
    _y = Image.new("RGBA", (400, 400), (250, 200, 60, 255))
    _g = Image.new("RGBA", (400, 400), (80, 200, 120, 255))
    _p, _ = P.make("rub", _y, _g)
    _im = Image.open(_p)
    _im.seek(0)
    _px = _im.convert("RGB").getpixel((39 + 37, 91 + 37))
    chk("rub 的头像没被白底盖掉", _px[0] > 200 and _px[1] > 150 and _px[2] < 120, _px)

print("== handle_petpet 口令直通 ==")
_SENT.clear()
h = bot.handle_petpet(900001, "摸摸头@小明", ["123456"], 777, [str(_face_path)], True)
chk("「摸摸头@小明」直接出图", h is True and len(_SENT) == 1, _SENT[-1:])
chk("发给了会话 900001", _SENT and _SENT[-1][0] == 900001)

_SENT.clear()
T._pp_state()["sent"].clear()
h = bot.handle_petpet(900002, "我咬了一口苹果", [], 777, [str(_face_path)], False)
chk("「我咬了一口苹果」不抢话", h is False and not _SENT)

h = bot.handle_petpet(900003, "今天天气不错", [], 777, [], False)
chk("普通聊天不触发", h is False)

_SENT.clear()
h = bot.handle_petpet(900004, "咬", [], 777, [str(_face_path)], False)
chk("单字动作没 @人 → 不触发（防误触）", h is False and not _SENT)

_SENT.clear()
h = bot.handle_petpet(900005, "咬@小明", ["123456"], 777, [str(_face_path)], True)
chk("单字动作 + @人 → 触发", h is True and len(_SENT) == 1)

print("== 「@我 模板」列清单 ==")
_OUT = []
_real_send = bot.send_group
bot.send_group = lambda gid, text, *a, **kw: (_OUT.append(str(text)), True)[1]
try:
    _OUT.clear()
    h = bot.handle_petpet_list(900020, "模板", [], 777, False)
    chk("没 @我 时「模板」不抢话", h is False and not _OUT)

    _OUT.clear()
    h = bot.handle_petpet_list(900020, "模板", [], 777, True)
    _txt = _OUT[-1] if _OUT else ""
    chk("「@我 模板」列出清单", h is True and "捏脸互动" in _txt, _txt[:40])
    chk("清单带编号且条数对得上",
        _txt.count("\n") >= len(P.list_templates())
        and " 1." in _txt and "13." in _txt, _txt.count("\n"))
    chk("清单里每个模板名都在",
        all(x["name"] in _txt for x in P.list_templates()))

    _OUT.clear()
    h = bot.handle_petpet_list(900020, "有什么模板", [], 777, True)
    chk("「有什么模板」不当清单口令（交给大脑）", h is False and not _OUT)

    _SENT.clear()
    _real_fetch = P.fetch_avatar
    P.fetch_avatar = lambda qq, timeout=12: face     # 「模板 N」这条路会真去拉头像，打桩成假脸
    try:
        h = bot.handle_petpet_list(900021, "模板 3", [], 777, True)
    finally:
        P.fetch_avatar = _real_fetch
    chk("「模板 3」按编号出图", h is True and len(_SENT) == 1, _SENT[-1:])

    _OUT.clear()
    h = bot.handle_petpet_list(900022, "模板 99", [], 777, True)
    chk("编号越界给提示", h is True and _OUT and "没有第 99 个" in _OUT[-1], _OUT[-1:])
finally:
    bot.send_group = _real_send

print("== 冷却与额度 ==")
_SENT.clear()
bot.handle_petpet(900006, "敲一敲", [], 777, [str(_face_path)], False)
_SENT.clear()
h = bot.handle_petpet(900006, "敲一敲", [], 777, [str(_face_path)], False)
chk("同会话冷却内不再出图", h is False and not _SENT)
_SENT.clear()
h = bot.handle_petpet(900007, "敲一敲", [], 777, [str(_face_path)], False)
chk("换个会话照常出图", h is True and len(_SENT) == 1)

_old = bot.CFG["skills"].setdefault("settings", {}).setdefault("petpet", {})
_old["maxPerHour"] = 1
_old["cooldown"] = 0
_SENT.clear()
h = bot.handle_petpet(900008, "拍拍", [], 777, [str(_face_path)], False)
h2 = bot.handle_petpet(900008, "拍拍", [], 777, [str(_face_path)], True)
chk("每小时额度 1 → 第二次被挡且被告知原因", h is True and h2 is True and len(_SENT) == 1, _SENT)
_old.pop("maxPerHour", None)
_old.pop("cooldown", None)

print("== 关掉技能 ==")
S.set_on("petpet", False)
_SENT.clear()
h = bot.handle_petpet(900009, "摸摸头", [], 777, [str(_face_path)], True)
chk("关掉后口令不认", h is False and not _SENT)
o, msg = T.call("petpet", "send", cid=900009, qq="10001", template="knock")
chk("关掉后工具也拒绝", (not o) and "关" in msg, msg)
S.set_on("petpet", True)

print("== 素材缺失降级 ==")
if "support" not in ready:
    o, msg = T.call("petpet", "send", cid=900010, qq="10001", template="support")
    chk("素材缺失时给的是人话", (not o) and "素材" in msg, msg)
else:
    print("  （support 素材已有，跳过）")

print("== 错误输入 ==")
o, msg = T.call("petpet", "send", cid=900011, template="knock")
chk("不给 QQ 号要报错", (not o) and "QQ" in msg, msg)
o, msg = T.call("petpet", "send", cid=900011, qq="10001", template="飞踢")
chk("不认识的动作列出可选", (not o) and "没有" in msg, msg)

print("== 跟上游对素材（2026-10-26 晚） ==")
# 全程不联网：_sync_get 换成假清单 + 假图，但**比对逻辑是真的**（md5 逐字节算）。
P.bind(state_dir=str(_tmp))
P.ASSETS = _tmp / "assets"
if not P.ASSETS.exists():
    shutil.copytree(Path(_ROOT) / "skills" / "petpet" / "assets", P.ASSETS)
    # 只留内置那 13 个：主人后来从「素材市场」搬进来的（hug_leg / kick_ball …）
    # 会让下面的 status 断言（ready / portable）跟着线上状态变，测试就不稳了。
    for _d in list(P.ASSETS.iterdir()):
        if _d.is_dir() and _d.name not in P._PRISTINE:
            shutil.rmtree(_d, ignore_errors=True)
_md5_real = hashlib.md5((P.ASSETS / "petpet" / "0.png").read_bytes()).hexdigest()
_md5_real2 = hashlib.md5((P.ASSETS / "petpet" / "1.png").read_bytes()).hexdigest()
_BODY_OK = b"\x89PNG\r\n\x1a\n fake-new-file"
_BODY_BAD = b"this body does not match its hash"
_MD5_OK = hashlib.md5(_BODY_OK).hexdigest()
_CALLS = []


def _fake_get(path, timeout=None):
    _CALLS.append(path)
    if path == "resource_list.json":
        return json.dumps([
            {"path": "images/petpet/0.png", "hash": _md5_real},      # 一致 → 跳过
            {"path": "images/petpet/1.png", "hash": _md5_real2},     # 一致 → 跳过
            {"path": "images/petpet/8.png", "hash": "0" * 32},       # 有文件但 md5 不符 → 换
            {"path": "images/pat/99.png", "hash": _MD5_OK},          # 本地缺 → 下载
            {"path": "images/addiction/0.png", "hash": _MD5_OK},     # 上游独有模板
        ]).encode("utf-8")
    if path == "images/pat/99.png":
        return _BODY_OK
    if path == "images/petpet/8.png":
        return _BODY_BAD
    raise RuntimeError("假镜像没有 %s" % path)


P._sync_get = _fake_get
(P.ASSETS / "petpet" / "8.png").write_bytes(b"old body")
st0 = P.sync_status()
chk("状态字段齐", all(k in st0 for k in ("templates", "local_ready", "upstream_total",
                                         "upstream_only", "running", "auto")), list(st0))
chk("本地就绪数 = 模板数", st0["local_ready"] == st0["templates"], st0["local_ready"])

P.sync_start()
_t0 = _t.time()
while P._SYNC.get("running") and _t.time() - _t0 < 30:
    _t.sleep(0.05)
st1 = P.sync_status()
chk("同步跑完", not st1["running"], st1["msg"])
chk("缺的文件下载下来了", (P.ASSETS / "pat" / "99.png").exists()
    and (P.ASSETS / "pat" / "99.png").read_bytes() == _BODY_OK)
chk("md5 一致的不重下", "images/petpet/0.png" not in _CALLS
    and "images/petpet/1.png" not in _CALLS, _CALLS)
chk("md5 不符的不写坏文件",
    (P.ASSETS / "petpet" / "8.png").read_bytes() == b"old body")
chk("改过的算 1 个更新", st1["added"] == 1, (st1["added"], _CALLS))
chk("查过 4 个文件（按文件数算）", st1["checked"] == 4, st1["checked"])
chk("上游独有的模板被记下来", "addiction" in st1["upstream_only"], st1["upstream_only"])
chk("上游总数 3 个模板（按模板算，含没搬的）", st1["upstream_total"] == 3, st1["upstream_total"])
chk("记账文件写下来了", (Path(_tmp) / "petpet" / "sync.json").exists())

print("== 素材市场：解析上游参数（2026-10-27） ==")
from qbot import qbot_petpet_port as PORT   # noqa: E402

_SRC = '''
def hug_leg(img: BuildImage = UserImg(), arg=NoArg()):
    img = img.convert("RGBA").square().resize((78, 78))
    locs = [(20, 100, 78, 78), (24, 96, 80, 80)]
    frames = []
    for i in range(2):
        frame = load_image(f"hug_leg/{i}.png")
        x, y, w, h = locs[i]
        frame.paste(img.resize((w, h)), (x, y), below=True)
        frames.append(frame.image)
    return save_gif(frames, 0.05)


def capoo_rub(img: BuildImage = UserImg(), arg=NoArg()):
    img = img.convert("RGBA").square().resize((180, 180))
    locs = [(178, 184, 78, 260)]
    frames = []
    for i in range(1):
        frame = load_image(f"capoo_rub/{i}.png")
        w, h, x, y = locs[i]
        frame.paste(img.resize((w, h)), (x, y), below=True)
        frames.append(frame.image)
    return save_gif(frames, 0.1)


def always(img: BuildImage = UserImg(), arg=NoArg()):
    frame = BuildImage.new("RGBA", (500, 100), "white")
    frame.draw_text((20, 20, 200, 60), "要我一直", max_fontsize=60)
    return frame.save_jpg()


def paint(img: BuildImage = UserImg(), arg=NoArg()):
    img = img.convert("RGBA").resize((117, 135), keep_ratio=True)
    frame = load_image("paint/0.png")
    frame.paste(img.rotate(4, expand=True), (95, 107), below=True)
    return frame.save_jpg()


def rip_angrily(img: BuildImage = UserImg(), arg=NoArg()):
    img = img.convert("RGBA").square().resize((105, 105))
    frame = load_image("rip_angrily/0.png")
    frame.paste(img.rotate(-24, expand=True), (18, 170), below=True)
    frame.paste(img.rotate(24, expand=True), (163, 65), below=True)
    return frame.save_jpg()
'''
_ok1, _why1 = PORT.analyze(_SRC, "hug_leg")
chk("能抠出帧数/位置/时长", _ok1 and _ok1["frames"] == 2 and len(_ok1["locs"]) == 2
    and _ok1["duration"] == 50 and _ok1["kind"] == "under", (_ok1, _why1))
chk("抠出中文名", _ok1 and _ok1["name"] == "抱大腿", _ok1 and _ok1["name"])
_ok2, _ = PORT.analyze(_SRC, "capoo_rub")
chk("(w,h,x,y) 那种认出 whxy", _ok2 and _ok2.get("loc_order") == "whxy", _ok2)
_ok3, _why3 = PORT.analyze(_SRC, "always")
chk("要排文字的不硬搬，给出原因", _ok3 is None and "文字" in (_why3 or ""), _why3)
# 2026-10-27：旋转（expand=True）从「不接」改成能抠 —— support / paint / overtime 那批
_ok4, _why4 = PORT.analyze(_SRC, "paint")
chk("抠出旋转角度", _ok4 and _ok4.get("rotate") == 4, (_ok4, _why4))
chk("认出「先缩放再转」", _ok4 and _ok4.get("rotate_after_resize") is True, _ok4)
_ok5, _why5 = PORT.analyze(_SRC, "rip_angrily")
chk("一帧贴两个角度的老实拒", _ok5 is None and "两个角度" in (_why5 or ""), _why5)
chk("不存在的模板给原因", PORT.analyze(_SRC, "nope")[0] is None)

print("== 素材市场：搬进来 / 移除 / 放回去 ==")
# 假上游：两个模板各 2 帧真图（用 PIL 画，走真解析真渲染，只把网络换成假的）
from PIL import Image as _Im, ImageDraw as _Drw  # noqa: E402
_FR = {}
for _tn, _nf in (("hug_leg", 2), ("kick_ball", 2), ("always", 1)):
    for _i in range(_nf):
        _im = _Im.new("RGBA", (120, 120), (0, 0, 0, 0))
        _Drw.Draw(_im).rectangle((10, 10, 110, 110), outline=(0, 0, 0, 255), width=3)
        _buf = io.BytesIO()
        _im.save(_buf, "PNG")
        _FR[("images/%s/%d.png" % (_tn, _i))] = _buf.getvalue()

_MKT_LIST = {"hug_leg": ["0.png", "1.png"], "kick_ball": ["0.png", "1.png"],
             "always": ["0.png"]}
_UP_SPEC = {
    "hug_leg": {"name": "抱大腿", "frames": 2, "duration": 50, "prep": "square",
                "kind": "under", "locs": [(20, 20, 78, 78), (24, 24, 80, 80)],
                "words": ["抱大腿"]},
    "kick_ball": {"name": "踢球", "frames": 2, "duration": 60, "prep": "square",
                  "kind": "under", "locs": [(30, 30, 60, 60), (32, 32, 62, 62)],
                  "words": ["踢球"]},
    "always": {"_why": "要往图上排版文字"},
}
_orig_get, _orig_list, _orig_analyze = P._sync_get, P._up_listing, P._up_analyze
P._sync_get = lambda path, timeout=None: _FR[path]
P._up_listing = lambda force=False: _MKT_LIST
P._up_analyze = lambda force=False: _UP_SPEC

r1 = P.import_template("hug_leg")
chk("搬进来了", r1.get("ok") and r1.get("files") == 2, r1)
chk("素材真落盘了", (P.ASSETS / "hug_leg" / "0.png").exists())
chk("记账写进 templates.json",
    "hug_leg" in json.loads((Path(_tmp) / "petpet" / "templates.json").read_text(encoding="utf-8")))
chk("搬完能认口令", P.resolve("抱大腿")[0] == "hug_leg", P.resolve("抱大腿"))
_p, _n = P.make("hug_leg", P._dummy_avatar())
chk("搬完真能出图", Path(_p).exists() and _n == "抱大腿")
chk("重复搬要拒", P.import_template("hug_leg").get("ok") is False)
_r2 = P.import_template("always")
chk("需手工的不硬搬，原因带出来", _r2.get("ok") is False and "文字" in _r2.get("error", ""), _r2)

c = P.catalog()
_ids = {x["id"]: x for x in c["items"]}
chk("市场里能看到状态", _ids["hug_leg"]["status"] == "ready"
    and _ids["kick_ball"]["status"] == "portable"
    and _ids["always"]["status"] == "manual", _ids["always"])
chk("市场统计对得上", c["ready"] >= 1 and c["portable"] == 1, (c["ready"], c["portable"]))

r3 = P.remove_template("hug_leg")
chk("移除成功", r3.get("ok"), r3)
chk("素材没删，只是挪到 _removed/", (P.ASSETS / "_removed" / "hug_leg" / "0.png").exists()
    and not (P.ASSETS / "hug_leg").exists())
chk("移除后认不出口令", P.resolve("抱大腿")[0] != "hug_leg", P.resolve("抱大腿"))
chk("移除后不在可用模板里", "hug_leg" not in P.TEMPLATES)
chk("参数留底没删（放回来要用）", "hug_leg" in P.ported_names(), P.ported_names())
P.bind(state_dir=str(_tmp))
chk("重启后依然是移除状态", "hug_leg" not in P.TEMPLATES and "hug_leg" in P.removed_names())
r4 = P.restore_template("hug_leg")
chk("放回来成功", r4.get("ok") and (P.ASSETS / "hug_leg" / "0.png").exists(), r4)
chk("放回来又能认口令", P.resolve("抱大腿")[0] == "hug_leg", P.resolve("抱大腿"))
P.remove_template("hug_leg", hard=True)
chk("硬移除连素材一起删", not (P.ASSETS / "_removed" / "hug_leg").exists())
P.restore_template("hug_leg")

print("== 素材市场：动图预览（2026-10-27 主人说「还是静态的」）==")
chk("帧号解析：0.png/01.png/2.png 都认",
    P._frame_ids(["0.png", "01.png", "2.png", "icon.ttf"]) == [0, 1, 2],
    P._frame_ids(["0.png", "01.png", "2.png", "icon.ttf"]))
_smp = P._sample(list(range(100)), 10)
chk("帧太多就均匀抽", len(_smp) == 10 and _smp[0] == 0 and _smp[-1] >= 90, _smp)
_sc = P._scale_spec({"locs": [(100, 100, 50, 50)], "resize": (200, 200),
                     "y_shift": 10}, 0.5)
chk("参数按「缓存/原图」比例缩回去",
    _sc["locs"][0] == (50, 50, 25, 25) and _sc["resize"] == (100, 100)
    and _sc["y_shift"] == 5, _sc)
chk("s=1 时参数原样不动", P._scale_spec({"locs": [(1, 2, 3, 4)]}, 1.0)["locs"][0] == (1, 2, 3, 4))
P._meta_set("kick_ball", (120, 120))
chk("坐标在画面里 → 能搬", P._loc_ok("kick_ball", {"locs": [(30, 30, 60, 60)]}) is True)
chk("坐标出界 → 不算能搬（distracted 那种）",
    P._loc_ok("kick_ball", {"locs": [(30, 300, 60, 60)]}) is False)
chk("原图尺寸不知道时先放行", P._loc_ok("nope_nope", {"locs": [(9999, 9999)]}) is True)
chk("洋红头像数得出来",
    P._avatar_seen([_Im.new("RGBA", (10, 10), (0, 0, 0, 255))]) is False
    and P._avatar_seen([_Im.new("RGBA", (10, 10), (255, 0, 255, 255))]) is True)
for _i in (0, 1):
    P._fetch_frame("kick_ball", _i)
_pv = P.demo_gif("kick_ball")
chk("没搬进来的模板也能出预览动图", bool(_pv) and Path(_pv).exists()
    and Path(_pv).suffix == ".gif", _pv)
chk("预览动图是多帧 GIF", _pv and _Im.open(_pv).n_frames == 2,
    _pv and _Im.open(_pv).n_frames)
chk("预览动图不超过 200px", _pv and max(_Im.open(_pv).size) <= 200, _pv and _Im.open(_pv).size)
_pv2 = P.demo_gif("kick_ball")
chk("同一张动图走缓存（不重做）", _pv2 == _pv)
chk("动图进度能查", isinstance(P.demo_status().get("cached"), int), P.demo_status())
P._DEMO.update(running=False, done=0, total=0, built=0, failed=0)
_st = P.demo_prefetch(workers=2)
chk("后台能整页预取", _st.get("ok") is True, _st)
for _ in range(300):
    if not P._DEMO.get("running"):
        break
    _t.sleep(0.05)
chk("后台预取跑完、没有失败", P._DEMO.get("running") is False
    and P._DEMO.get("failed") == 0, dict(P._DEMO))

# 异步搬：同一时间只许一个
_orig_imp = P.import_template
P.import_template = lambda n: (_t.sleep(0.2) or
                               {"ok": True, "id": n, "name": n, "files": 1, "detail": "假"})
P._PORT.update(running=False)
a = P.import_start("kick_ball")
chk("异步搬能起来", a.get("ok") and a.get("started"), a)
chk("还在搬的时候不许再起一个", P.import_start("kick_ball").get("ok") is False)
for _ in range(60):
    if not P._PORT.get("running"):
        break
    _t.sleep(0.05)
chk("搬完状态回落", P._PORT.get("running") is False and P._PORT.get("result", {}).get("ok"))
chk("状态接口带清单", P.market_status().get("ok") and "ported" in P.market_status())
P.import_template = _orig_imp
P._sync_get, P._up_listing, P._up_analyze = _orig_get, _orig_list, _orig_analyze
P._PORT.update(running=False, result=None)

print("== 每天自动对一遍 ==")
_at4 = _t.localtime(_t.mktime((2026, 10, 27, 4, 0, 0, 0, 0, -1)))
_at5 = _t.localtime(_t.mktime((2026, 10, 27, 5, 0, 0, 0, 0, -1)))
_orig_start = P.sync_start
P.sync_start = lambda: {"ok": True, "message": "假的"}
chk("不到点不跑", P.auto_tick(now=_at5) is False)
chk("到点跑一次", P.auto_tick(now=_at4) is True)
chk("同一天不重复跑", P.auto_tick(now=_at4) is False)
_auto = json.loads((Path(_tmp) / "petpet" / "auto.json").read_text(encoding="utf-8"))
chk("先记账（日期先写死）", _auto.get("date") == "2026-10-27", _auto.get("date"))
_orig_status = P.auto_status
P.auto_status = lambda: {"enabled": False, "hour": 4, "date": "", "msg": "", "added": 0, "at": 0}
chk("关掉 autoSync 就不碰网络", P.auto_tick(now=_at4) is False)
P.auto_status = _orig_status
_real_assets = P.ASSETS
P.ASSETS = _tmp / "empty_assets"
chk("一个模板都没有时不自作主张灌 1000 个文件", P.auto_tick(now=_at4) is False)
P.ASSETS = _real_assets
P.sync_start = _orig_start

print("== 素材市场网页：移除后必须走「放回」==")
_admin = open(Path(_ROOT) / "qbot" / "admin_html.py", encoding="utf-8").read()
chk("已移除的模板显示「放回」而不是「搬过来」", 'if(x.removed){' in _admin and 'act("restore",x);}' in _admin)
chk("恢复成功的提示存在", "已放回" in _admin)
chk("移除提示不再叫人点「搬过来」", "想要了再点「放回」" in _admin)

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
