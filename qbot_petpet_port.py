# -*- coding: utf-8 -*-
"""qbot_petpet_port —— 把上游 `functions.py` 里的模板参数**读出来**（不跑它的代码）。

为什么要有这层：捏脸素材是「帧图 + 参数」两半，帧图能从镜像直接下，但**贴哪儿、几帧、
盖上面还是垫下面**这些参数写在上游 Python 里。手工一个个抄 99 个不现实，所以这里用 ast
把「能证明的写法」抠成 `qbot_petpet.TEMPLATES` 认的那套字段。

**只认能证明的**：认不出来就老实返回原因（`需手工`），绝不猜一个凑数 —— 猜错的结果是
群友被贴一脸歪头像。上游 120 个函数里，约 45 个落在下面这几类里：

    A. 帧序列 + 贴头像
       for i in range(N):
           frame = load_image(f"tpl/{i}.png")
           x, y, w, h = locs[i]
           frame.paste(img.resize((w, h)), (x, y), below=True)
       return save_gif(frames, 0.06)

    B. 单图 + 贴头像（jpg）
       frame = load_image("tpl/0.png")
       frame.paste(img.resize((w, h)), (x, y), alpha=True)
       return frame.save_jpg()

    C. 上面两种套一层 `def make(img): ... return frame.copy().paste(...)` + make_jpg_or_gif

明确不接（会明确报原因）：`perspective`（透视变形）、`draw_text`/`Text2Image`（要排版文字）、
`random`（随机参数）、`resize_width`/`filter`（要先算尺寸/滤镜）、`mirror`/`transpose`、
`BuildImage.new`（自己拼画布）、`frames = a[0:12] + b[...]`（帧序列是算出来的）、
双头像（`SenderImg`/`user_head`/`self_head`）。

用法：
    import qbot_petpet_port as PORT
    spec, why = PORT.analyze(src_text, "hug_leg")
"""
import ast
import re

# 上游函数名 → 中文名（只覆盖「能自动搬」的那批；没列的用英文 id 当名字）
CN_NAMES = {
    "petpet": "摸摸头", "pat": "拍拍", "bite": "咬一口", "knock": "敲一敲",
    "rub": "揉一揉", "roll": "滚", "twist": "扭", "thump": "捶",
    "punch": "揍一拳", "pound": "锤", "hammer": "砸", "smash": "重砸",
    "eat": "吃", "klee_eat": "可莉吃", "hutao_bite": "胡桃咬",
    "hug_leg": "抱大腿", "hold_tight": "抱紧", "tightly": "抱紧紧",
    "kiss": "亲亲", "decent_kiss": "深吻", "love_you": "爱你",
    "police": "警察", "police1": "警察", "anti_kidnap": "防拐",
    "back_to_work": "回去上班", "overtime": "加班", "potato": "土豆",
    "cover_face": "捂脸", "dont_touch": "别碰", "no_response": "没反应",
    "whyatme": "为啥是我", "perfect": "完美", "applaud": "鼓掌",
    "worship": "膜拜", "support": "支持你", "alike": "像",
    "rip": "撕", "rip_angrily": "生气撕", "scratch_head": "挠头",
    "thinkwhat": "想啥呢", "dinosaur": "恐龙", "wallpaper": "壁纸",
    "wooden_fish": "木鱼", "listen_music": "听歌", "karyl_point": "指",
    "kick_ball": "踢球", "chase_train": "追火车", "printing": "打印",
    "blood_pressure": "血压", "walnutpad": "核桃板", "walnut_zoom": "核桃放大",
    "fencing": "击剑", "suck": "吸", "prpr": "舔屏", "paint": "画画",
    "painter": "画家", "need": "需要", "distracted": "分心", "play": "玩",
    "capoo_rub": "咖波揉", "windmill_turn": "风车转", "throw": "扔出去",
    "throw_gif": "扔出去", "crawl": "爬", "always": "要我一直",
    "always_like": "一直喜欢", "loading": "加载中", "littleangel": "小天使",
    "play_game": "玩游戏", "name_generator": "生成名字", "repeat": "复读",
    "safe_sense": "安全意识", "sit_still": "坐好", "tankuku_holdsign": "举牌",
    "teach": "教", "together": "一起", "turn": "转", "marriage": "结婚",
    "mywife": "我老婆", "my_friend": "我朋友", "make_friend": "交朋友",
    "bocchi_draft": "波奇草稿", "bubble_tea": "奶茶", "captain": "队长",
    "china_flag": "国旗", "confuse": "迷惑", "coupon": "优惠券",
    "garbage": "垃圾", "gun": "枪", "hit_screen": "打屏幕", "incivilization": "不文明",
    "interview": "采访", "jiji_king": "叽叽王", "jiujiu": "啾啾",
    "kirby_hammer": "卡比锤", "learn": "学习", "look_this_icon": "看这图标",
    "need_kiss": "求亲亲", "paint_deer": "画鹿", "read_book": "读书",
    "rise_dead": "诈尸", "suck_blood": "吸血", "tightly_hold": "抱紧",
    "what_is_this": "这是啥", "wooden_fish_beat": "敲木鱼",
}

# 出现即「不接」的写法 → 给人看的理由（在**函数源码文本**上匹配，比 ast.dump 好读也好写）
_REJECT = (
    ("perspective", "要透视变形（perspective）"),
    ("draw_text", "要往图上排版文字"),
    ("Text2Image", "要往图上排版文字"),
    ("Font(", "要往图上排版文字"),
    ("random", "参数是随机的"),
    ("resize_width", "要按原图比例算尺寸"),
    ("filter(", "要先加滤镜"),
    ("mirror", "要镜像翻转"),
    ("transpose", "要旋转/翻转整帧"),
    ("BuildImage.new", "自己拼了一张新画布"),
    ("SenderImg", "要两个头像（发起人 + 被 @ 的）"),
    ("self_head", "要两个头像（发起人 + 被 @ 的）"),
    ("user_head", "要两个头像（发起人 + 被 @ 的）"),
)


def _const(node):
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        return None


def _nums(node):
    if isinstance(node, ast.Tuple):
        vals = [_const(e) for e in node.elts]
        if vals and all(isinstance(v, (int, float)) for v in vals):
            return tuple(int(v) if isinstance(v, int) else v for v in vals)
    return None


def _list_of_tuples(node):
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    out = []
    for e in node.elts:
        t = _nums(e)
        if t is None:
            return None
        out.append(t)
    return out


def _dump(body):
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def _find_def(tree, name):
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == name:
            return fn
    return None


def _unpack_order(loop):
    """看循环里 `x, y, w, h = locs[i]` 的**变量名顺序**，决定 loc 是 xywh 还是 whxy。"""
    for node in ast.walk(loop):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple):
            names = [getattr(t, "id", "") for t in node.targets[0].elts]
            if len(names) in (3, 4) and set(names) <= {"x", "y", "w", "h", "a", "angle"}:
                return "".join(names)
    return ""


def _paste_kind(fn):
    """below=True → under（头像垫帧下面）；alpha=True → over_frame（头像盖帧上面）。"""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "paste":
            kw = {k.arg: _const(k.value) for k in node.keywords if k.arg}
            if kw.get("below") is True:
                return "under"
            if kw.get("alpha") is True:
                return "over_frame"
    return ""


def _avatar_size(fn):
    """`img.resize((w, h))` / `img.resize((w, h), keep_ratio=True)` 里的固定尺寸。"""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "resize":
            if node.args:
                t = _nums(node.args[0])
                if t and len(t) == 2:
                    keep = any(k.arg == "keep_ratio" and _const(k.value) is True
                               for k in node.keywords)
                    return t, keep
    return None, False


def _avatar_rotate(fn):
    """`img.rotate(23, expand=True)` 里的固定角度。

    返回 (角度, 原因)。一个函数里出现**两个不同角度**（rip 那种同一张头像贴两处）
    就认不出来，老实拒。旋转和缩放的先后顺序另算，见 `_rotate_before_resize`。
    """
    angles = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") != "rotate":
            continue
        if not node.args:
            continue
        a = _const(node.args[0])
        if not isinstance(a, (int, float)):
            return None, "旋转角度是算出来的"
        angles.add(a)
    if not angles:
        return None, ""
    if len(angles) > 1:
        return None, "一帧要贴两次（两个角度）"
    return angles.pop(), ""


def _rotate_before_resize(fn):
    """`img.rotate(7.5, expand=True).resize((225, 225))` —— 先转后缩放（karyl_point 那种）。

    其余写法（`resize(...).rotate(...)`、或者 resize 先赋值再 rotate）都是先缩放后转。
    顺序不一样贴出来大小就不一样，因为旋转会把画布撑大。
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "resize":
            src = node.func.value
            if isinstance(src, ast.Call) and getattr(src.func, "attr", "") == "rotate":
                return True
    return False


def _duration(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and \
                (getattr(node.func, "attr", "") or getattr(node.func, "id", "")) == "save_gif":
            if len(node.args) >= 2:
                v = _const(node.args[1])
                if isinstance(v, (int, float)) and v > 0:
                    return int(round(v * 1000))
    return None


def _is_jpg(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            nm = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if nm in ("save_jpg", "make_jpg_or_gif"):
                return True
    return False


def _frame_count(fn):
    """帧数：`for i in range(N)` 取最大 N；`range(1, N)` 也认；没有循环 = 单帧。"""
    best = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) \
                and getattr(node.iter.func, "id", "") == "range":
            args = [_const(a) for a in node.iter.args]
            if len(args) == 1 and isinstance(args[0], int):
                best = max(best, args[0])
            elif len(args) == 2 and all(isinstance(a, int) for a in args):
                best = max(best, args[1] - args[0])
    return best or 1


def _loc_by_index(fn):
    """`locs[1] if i == 2 else locs[0]` → {2: 1}（pat 那种「第 3 张换个位置」）。"""
    out = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.IfExp):
            t = node.body
            cmp = node.test
            if isinstance(t, ast.Subscript) and isinstance(cmp, ast.Compare):
                try:
                    j = _const(t.slice)
                    k = _const(cmp.comparators[0])
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(j, int) and isinstance(k, int):
                    out[k] = j
    return out


def analyze(src, name, tree=None):
    """从上游源码里抠出 `name` 这个模板的参数。

    返回 (spec, "") 或 (None, "为什么搬不了")。spec 的字段和 `qbot_petpet.TEMPLATES` 一致。
    `tree` 可传已解析好的 ast（`available()` 批量调时用，省得把 87KB 源码 parse 120 遍）。
    """
    try:
        tree = tree or ast.parse(src)
    except SyntaxError as e:
        return None, "上游源码解析失败：%s" % e
    fn = _find_def(tree, name)
    if fn is None:
        return None, "上游没有这个模板函数"
    lines = src.splitlines()
    body_src = "\n".join(lines[fn.lineno - 1:(fn.end_lineno or fn.lineno)])
    for pat, why in _REJECT:
        if pat in body_src:
            return None, why
    # 帧顺序是算出来的（a[0:12] + b[...]），不接
    for node in ast.walk(fn):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if isinstance(node.left, (ast.Subscript, ast.List)):
                return None, "帧顺序是拼接算出来的"
    # maker 模式：make_gif_or_combined_gif(img, maker, 24, ...) —— 帧数在第三个参数，
    # 函数体里没有 for range，帧数会算成 1（walnut_zoom 就是 24 帧 gif 里只贴第 1 帧）。
    if "make_gif_or_combined_gif" in body_src and _frame_count(fn) == 1:
        return None, "帧是 maker 拼的（认不出帧数）"
    # 两段 for（前 N 帧不贴头像、后面才贴），帧号对不上
    nfor = sum(1 for node in ast.walk(fn) if isinstance(node, ast.For))
    if nfor > 1:
        return None, "帧序列分好几段（前几帧不贴头像）"

    kind = _paste_kind(fn)
    if not kind:
        return None, "没看到 frame.paste(..., below=/alpha=)"
    prep = "circle" if ".circle()" in body_src else "square"
    dur = _duration(fn)
    jpg = _is_jpg(fn)
    frames = _frame_count(fn)

    # 位置表
    lists = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            lt = _list_of_tuples(node.value)
            if lt:
                lists[getattr(node.targets[0], "id", "")] = lt
    locs = lists.get("locs") or lists.get("user_locs")
    order = ""
    if locs is None:
        # 单帧直贴：frame.paste(img, (x, y), below=True)
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "paste" \
                    and len(node.args) >= 2:
                t = _nums(node.args[1])
                if t:
                    locs, order = [t], "xy"
                    break
    if not locs:
        return None, "找不到位置表（locs）"
    if any(isinstance(x, list) for x in locs):
        return None, "一帧要贴好几张（残影）"

    # loc 的排列顺序：默认 (x,y,w,h)，capoo_rub 那种 (w,h,x,y) 要换回来
    names = ""
    for node in ast.walk(fn):
        if isinstance(node, ast.For):
            names = _unpack_order(node) or names
    if not order:
        order = names or "xy"
    if len(locs[0]) == 4:
        order = names if names in ("xywh", "whxy") else "xywh"
    elif len(locs[0]) == 3:
        order = "xya"
    else:
        order = "xy"

    size, keep = _avatar_size(fn)
    ang, ang_why = _avatar_rotate(fn)
    if ang_why:
        return None, ang_why
    # 只有 1 个位置、却有多帧（eat 那种「每帧贴同一个位置」）⇒ 位置复制到每一帧。
    # 注意：如果位置表比帧数少**但不是 1**，说明有「前几帧不贴」的分段，认不出来就拒。
    if len(locs) == 1 and frames > 1:
        locs = [locs[0]] * frames
    elif 1 < len(locs) < frames and not _loc_by_index(fn):
        return None, "位置比帧数少（有几帧不贴头像），认不出来"
    spec = {
        "name": CN_NAMES.get(name) or name,
        "frames": frames,
        "prep": prep,
        "kind": kind,
        "locs": [tuple(x) for x in locs],
        "words": [name],
        "_port": "auto",          # 自动搬的标记（手工搬的写 manual）
    }
    if order == "whxy":
        spec["loc_order"] = "whxy"
    if dur:
        spec["duration"] = dur
    if jpg:
        spec["jpg"] = True
    if size:
        if keep:
            spec["resize_keep"] = size
        else:
            spec["resize"] = size
    if ang:
        spec["rotate"] = ang
        if size and not _rotate_before_resize(fn):
            # 上游是「先缩放再转」：旋转会把画布撑大，顺序错了贴出来大小不对
            spec["rotate_after_resize"] = True
    bi = _loc_by_index(fn)
    if bi:
        spec["loc_by_index"] = bi
    return spec, ""


def available(src):
    """整份源码 → {模板名: spec 或 {"_why": 原因}}，用来在网页上标「可搬 / 需手工」。

    ⚠️ 只 parse 一次再逐个函数分析：120 个函数 × parse 87KB 源码 ≈ 十几秒，
    网页每开一次抽屉就等十几秒是不能接受的。
    """
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef):
            spec, why = analyze(src, fn.name, tree)
            out[fn.name] = spec if spec else {"_why": why}
    return out
