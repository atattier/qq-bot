"""把「一句话」或「一张图」变成 MOSS 的 Layout 提示词，再交给 moss_gen 出图。

这是 E:\\AI\\moss-prompt-studio 那套提示词工程的复用 —— **不复制代码**，
Studio 那边改了（尤其是主人在界面里调过的 skill），这边自动跟着变：

| 用途 | 来源 | 依赖 |
|---|---|---|
| 编译规则（草稿 → Moss JSON 提示词） | `vendor/moss-image-prompt-enhancer/scripts/compile_prompt.py`（官方脚本） | stdlib |
| 草稿校验 / 编译 / 镜头 | `server/layout_core.py` | stdlib + 上面那个 |
| 图像反推的坐标协议与归一化 | `server/reverse_boxes.py` | stdlib |
| 提示词正文 | `data/skills.json` 里**当前生效**的 layout 技能 | 纯 JSON |

⚠️ 只有这三个是 stdlib-only 才敢直接 import；`server/llm.py` / `agent.py` / `api.py`
   都拖着 httpx / fastapi / pydantic，**不要碰**（bot 的解释器没装，也装不上 3.14 的包）。
   LLM 调用由调用方注入回调（跟 comfy_gen.generate(llm=...) 一个套路）。

两种用法（都返回可直接喂给 Moss 的提示词）：
  from_text("一只橘猫坐在窗台，夕阳", 1024, 1024, llm_call=...)
  from_image("x.png", 1024, 1024, vision_call=...)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Studio 可能落在几个位置（主人搬过目录）。逐个试，别在第一个不存在的地方就判死。
# 🔴 2026-10-31 真事故：config 里写死的老路径 `E:\AI\moss-prompt-studio` 早被搬到
#    `E:\AI\MossTool\moss-prompt-studio`，可这里只认 config 那一个值 ⇒ 每次布局都抛
#    「找不到 MOSS Prompt Studio 目录」⇒ **静默回落成「把用户原话直接发给 Moss」**，
#    主人看到的就是「画个妄想天使三人组」六个字原样送去出图（403）。
STUDIO_CANDIDATES = [
    r"E:\AI\02_AI视频与视觉创作\MossTool\moss-prompt-studio",
    r"E:\AI\MossTool\moss-prompt-studio",
    r"E:\AI\moss-prompt-studio",
    r"E:\AI\Moss提示词skill\moss-prompt-studio",
]
STUDIO_DEFAULT = STUDIO_CANDIDATES[0]


def _studio_ok(d):
    """这个目录像不像 Studio：得有 server/layout_core.py 和 data 两样。"""
    try:
        p = Path(d)
        return (p / "server" / "layout_core.py").is_file() and (p / "data").is_dir()
    except Exception:
        return False

# 内置兜底提示词（Studio 的 skills.json 读不到时用）。与 official-layout 一致。
_LAYOUT_SYSTEM_FALLBACK = """你是 MOSS-Image Layout 提示词助手。根据用户想法输出**严格合法**的 Layout 草稿 JSON（不要 Markdown）。
字段必须且仅能有:
width, height, bbox_coord_space:"abs", bbox_format:"xyxy",
summary:{caption, meta?, canvas?},
items:[{label:"object"|"character"|"text", caption, bbox:[x1,y1,x2,y2], text?(仅text), tags?, palette?}]
规则:
- width/height 为正整数且为 16 的倍数；使用用户给定尺寸。
- bbox 为画布左上角起的绝对像素 [x_min,y_min,x_max,y_max]，且在画布内。
- 禁止 children/parent/id 等未知字段。
- summary.caption 与各 item.caption 用英文描述可见外观（用户明确要求中文除外）；可见文字原文放 text 字段。
- items 至少 1 个；框可重叠。
只输出一个 JSON 对象。"""

# 书写铁律：从 NINE-99-nine/jiujiu-comfyui-workflows 的 MOSS prompting.md 提炼（2026-10-29 并入）。
# 追加在**任何** layout 技能后面 —— 主人在 Studio 里改的那份照样生效，这里只补它没写的部分。
_LAYOUT_RULES = """

—— 书写铁律（MOSS Layout 实测，追加生效，与上面冲突时以上面为准）——
1. 用户给的主体、数量、关系、介质、风格、排除项、画面里要出现的文字，一字不改地保留。
2. 位置由 bbox 决定：item 的 caption 里不要再写方位词（upper-left / at the top / in the center）和像素坐标。
3. 不写质量词（masterpiece / best quality / 8k / ultra detailed），不写空赞美（eye-catching / perfectly balanced）。
4. 不写过程说明、不写「此处填标题」这种占位、不写看不见的东西。
5. summary.caption 写这些：介质与用途、一个自洽的场景、背景层次与景深、统一视角与光向、色调与情绪、各元素如何构成一张图。
6. label=text 的条目：要画的字逐字放进 text（原语言、原标点，再长也不截断）；caption 只描述字形、字重、字距、材质、对比、可读性，不翻译、不改写、不加字。
7. 多人图（≥4 个角色）：summary 里显式否定格状站位（not a grid or three horizontal rows），并写清遮挡规则（可遮肩与下半身，绝不遮脸）；每条 item 的 caption 钉死「谁在谁的左/右/前后」。
8. 篇幅参考（不是配额）：summary 约 70–105 词，物件条目约 55–85 词，文字条目约 35–60 词；整份 JSON 落在 4000 字符上下，超一点没关系，别为凑字数注水，也别为压字数砍掉用户要画的文字。
9. 只输出一个 JSON 对象：首字符 { ，末字符 } ，不要 Markdown、不要代码围栏、不要 JSON 之外的任何文字，不要以「方案」「步骤」这类小标题开头。
"""

# 简易档：沉浸情景模式那种「一幕一个主角」的描述用这个。
# 完整版会切出 9~10 个框、写 25 秒；这个压到 5 个框 10 秒（2026-10-29 实测）。
_LAYOUT_RULES_BRIEF = """

—— 书写铁律（简易档：这一幕只有一两个主角）——
1. 只切 4~6 个框：一两个主角 + 环境大块（背景、光源、最多一件道具），不要逐件罗列小东西。
2. 用户点了多个人时，一人一个 character 框，各自 caption 只写那一个人的外观，绝不合并成一条。
3. summary.caption 30~50 词，item.caption 15~30 词，整份 JSON 控制在 1200 字符内。
4. 用户给的主体、动作、服装、要画的文字一字不改；位置交给 bbox，caption 里不写方位词和像素坐标。
5. 不写质量词（masterpiece / best quality / 8k），不写空赞美，不写看不见的东西、不写占位。
6. label=text 时文字逐字放进 text（原语言不截断），caption 只描述字形字重材质可读性。
7. 只输出一个 JSON 对象：首字符 { ，末字符 } ，不要 Markdown、不要代码围栏、不要 JSON 之外的文字。
"""

# layout 技能的合格线：得真在讲布局格式。Studio 里主人曾把 active layout 换成纯说话人设
# （一段 bbox/caption/JSON 都没提），模型只能猜格式 —— 2026-10-29 查到那就是「布局端点返回空/
# 回半截 JSON」的真凶，所以这里先验格式再决定用不用。
_LAYOUT_MARKS = ("bbox", "summary", "items", "caption")


def looks_like_layout(prompt):
    """这段提示词是不是真在讲 Layout 格式（提到两个以上关键字算数）。"""
    t = prompt or ""
    return sum(1 for m in _LAYOUT_MARKS if m in t) >= 2


NL2 = chr(10) * 2

# 反推细节档（照抄 server/api.py 的 LAYOUT_REVERSE_DETAIL）
U_SEP = (NL2 + NL2 + "【Caption 散文（先单独反推的一轮，对动作/姿态理解更准），"
         "定位框和整体描述的动作 / 姿态 / 互动要照这段来写】" + NL2)

REVERSE_DETAIL = {
    "long": ("Detail level: LONG. Write rich, vivid English captions for summary.caption and "
             "every item.caption — appearance, materials, lighting, pose, spatial relations. "
             "Keep visible text exact in the text field when label is text."),
    "medium": ("Detail level: MEDIUM. Balanced English captions — clear subject, scene, and key "
               "traits without excessive flourish. Keep visible text exact in the text field "
               "when label is text."),
    "short": ("Detail level: SHORT. Concise English captions — only essentials for summary and "
              "each item. Keep visible text exact in the text field when label is text."),
}

_LC = None
_RB = None


def studio_dir(cfg=None):
    """Studio 目录：config 的 `moss.studio_dir` 优先，**不成立就按候选表逐个试**。

    🔴 2026-10-31 为什么必须这样：配置里那个路径是主人早期填的，后来目录搬了家，
    而这里原来**无条件相信 config** ⇒ `_load_modules` 抛异常 ⇒ 上层静默回落成
    「原始描述直传 Moss」。这类「配置错一个路径，整条提示词工程静默没了」的故障
    在日志里只留一行 WARNING，主人在群里只看到画得很离谱的图 —— 不可接受。
    判据用 `_studio_ok()`（要 server/layout_core.py + data 两样都在），不只看目录存在。
    """
    c = str(((cfg or {}).get("studio_dir") or "") or "").strip()
    if c and _studio_ok(c):
        globals()["_LAST_NOTE"] = ""
        return c
    for d in STUDIO_CANDIDATES:
        if _studio_ok(d):
            globals()["_LAST_NOTE"] = ("config 的 studio_dir 不成立（%s），自动改用 %s" % (c, d)
                              if c else "按候选表找到 %s" % d)
            return d
    globals()["_LAST_NOTE"] = ""
    # 全都不成立：把 config 那个值还回去，让上层报错里能看到「你填的路径」
    return c or STUDIO_DEFAULT


globals()["_LAST_NOTE"] = ""


def _load_modules(cfg=None):
    """懒加载 layout_core / reverse_boxes（只在第一次用时挂 sys.path）。"""
    global _LC, _RB
    if _LC is not None and _RB is not None:
        return _LC, _RB
    d = studio_dir(cfg)
    if not Path(d).is_dir():
        raise RuntimeError("找不到 MOSS Prompt Studio 目录：%s（config 的 moss.studio_dir 可改）" % d)
    if d not in sys.path:
        sys.path.insert(0, d)
    from server import layout_core as LC          # noqa: E402
    from server import reverse_boxes as RB        # noqa: E402
    _LC, _RB = LC, RB
    return LC, RB


def available(cfg=None):
    """这条路现在能不能用（Studio 目录 + 两个模块都在）。"""
    try:
        _load_modules(cfg)
        return True
    except Exception:
        return False


def _strip_rules(text):
    """把技能提示词尾巴上那段「书写铁律」摘掉，只留讲格式的部分。

    手机站里主人存的技能已经把铁律粘进去了；再叠一次就是两份互相打架的篇幅要求
    （完整版说 4000 字符、简易档说 1200）。所以先摘干净，再按本次档位补一份。
    """
    i = (text or "").find("—— 书写铁律")
    return (text[:i] if i > 0 else text).rstrip()


def layout_system_prompt(cfg=None, brief=False):
    """当前该用的 layout 提示词 = 一份真在讲格式的技能 + 书写铁律。

    Studio 的「技能」里主人调过提示词（比如要求客观描述、不许评论风格），这里读的就是
    那份 —— 但**只读真的在讲 Layout 的那份**：2026-10-29 实测过，active layout 被换成
    纯说话人设（一段 bbox/caption/JSON 都没提），模型只能猜格式，十有八九回空或回半截。
    不合格就退 official-layout，再退内置兜底。
    """
    skills, active = {}, ""
    try:
        p = Path(studio_dir(cfg)) / "data" / "skills.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        active = (data.get("active") or {}).get("layout") or ""
        for s in data.get("skills") or []:
            sid = s.get("id")
            if sid:
                skills[sid] = (s.get("system_prompt") or "").strip()
    except Exception:
        pass
    base = _strip_rules(skills.get(active) or "")
    if base and not looks_like_layout(base):
        note = "active layout「%s」没在讲布局格式，退到 official-layout" % (active or "?")
        base = _strip_rules(skills.get("official-layout") or "")
    else:
        note = "active layout" if base else "内置兜底"
    if not looks_like_layout(base):
        base = _LAYOUT_SYSTEM_FALLBACK
        note = "内置兜底"
    layout_system_prompt.note = note + "＋" + ("简易档" if brief else "书写铁律")
    return base + (_LAYOUT_RULES_BRIEF if brief else _LAYOUT_RULES)


layout_system_prompt.note = ""


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)
_TRAIL_COMMA_RE = re.compile(r",\s*([\]}])")


def _close_open(s):
    """补上没收尾的引号和括号（模型话说一半被 max_tokens 掐断时就是这个样子）。"""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    out = s + ('"' if in_str else "")
    return out + "".join("]" if c == "[" else "}" for c in reversed(stack))


def _loads_repaired(s):
    """坏 JSON 试着救回来：逗号、闭合、截断三种病。救不动返回 None。"""
    cur = _TRAIL_COMMA_RE.sub(r"\1", s)
    for _ in range(40):
        try:
            return json.loads(_close_open(cur))
        except json.JSONDecodeError as e:
            pos = getattr(e, "pos", None)
            if not pos:
                return None
            k = max(cur.rfind("}", 0, pos), cur.rfind("]", 0, pos))
            if k < 2:
                return None
            cur = cur[:k].rstrip().rstrip(",")   # 丢掉出事那项，退回上一个完整元素
        except ValueError:
            return None
    return None


def extract_json(text):
    """从模型回复里抠出第一个 JSON 对象（容忍 ```json 围栏、前后废话、以及坏 JSON）。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    fixed = _loads_repaired(s)
    if fixed is not None:
        return fixed
    m = _JSON_OBJ_RE.search(s)
    if not m:
        raise ValueError("回复里没有 JSON：%s" % s[:200])
    try:
        return json.loads(m.group(0))
    except Exception:
        fixed = _loads_repaired(m.group(0))
        if fixed is not None:
            return fixed
        raise ValueError("JSON 坏了救不回来：%s" % s[:200])


def _ensure_wh(w, h):
    """画布尺寸夹到 16 的倍数（Moss 硬要求）。"""
    try:
        w = max(256, (int(w) // 16) * 16)
        h = max(256, (int(h) // 16) * 16)
    except Exception:
        w = h = 1024
    # 总像素 ≤ 2097152（Moss 约束），超了等比缩
    while w * h > 2097152:
        if w >= h:
            w = max(256, w - 16)
        else:
            h = max(256, h - 16)
    return w, h


def from_text(desc, width, height, llm_call, system=None, cfg=None, brief=False):
    """一句话 → Layout 草稿 dict（绝对像素 bbox，可直接 compile）。

    llm_call(messages) -> str：由调用方注入（bot 侧接 chat_once）。
    brief=True 走简易档（一幕一个主角，4~6 个框，布局这步快一半多）。
    """
    LC, _ = _load_modules(cfg)
    w, h = _ensure_wh(width, height)
    sys_prompt = system or layout_system_prompt(cfg, brief=brief)
    user = ("画布尺寸 %dx%d。用户想法：%s\n请按规则输出 Layout 草稿 JSON"
            "（bbox 用这块画布的绝对像素）。%s"
            % (w, h, desc,
               "走简易档：只切 4~6 个框，别逐件罗列小物件。" if brief else ""))
    raw = llm_call([{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user}])
    draft = extract_json(raw)
    draft.setdefault("width", w)
    draft.setdefault("height", h)
    draft["width"], draft["height"] = w, h
    draft.setdefault("bbox_coord_space", "abs")
    draft.setdefault("bbox_format", "xyxy")
    return LC.sanitize_draft(draft, mode="layout", width=w, height=h)


def from_image(image_path, width, height, vision_call, system=None, cfg=None, detail="medium",
               focus=None, prose=""):
    """一张图 → Layout 草稿 dict（视觉模型反推定位框）。

    vision_call(image_path, sys_prompt, user_text) -> str：由调用方注入
    （bot 侧接 reverser 的 see.* 视觉链）。

    `focus`：用户对这张图**想画成什么**（一句话要求）。给了就塞进 user 段，并明确
    「summary 也只写画面主体、不写界面元素」。
    🔴 2026-10-31 实测踩的坑：只把约束加进 system 段不够 —— 视觉模型照样在
       `summary.caption` 里写「一张聊天应用截图」，而 summary 是会进最终提示词的，
       等于又把「画截图」这个方向带回去了。所以必须连 user 段一起约束。
    """
    LC, RB = _load_modules(cfg)
    w, h = _ensure_wh(width, height)
    if detail not in REVERSE_DETAIL:
        detail = "medium"
    # 视觉模型给的是 0–1000 归一化坐标（它自己换算画布像素很不准，Studio 的结论）
    fmt = RB.box_format_for("gpt-4o")          # 非 gemini 系 → box_1000
    sys_prompt = (system or layout_system_prompt(cfg)) + "\n\n" + \
        RB.reverse_rules(fmt, None, None, REVERSE_DETAIL[detail])
    user = ("请看这张参考图，按要求输出 JSON：整体描述 + 每个可见元素的定位框"
            "（%s，0–1000 归一化）。detail=%s。" % (fmt, detail))
    # 混合档（照抄 server/api.py 的 hybrid 流程）：先跑一次 Caption 散文（对动作/姿态
    # 理解更准），把散文喂给定位框那轮做「优化」；坐标仍然只来自图上看到的东西。
    # 2026-11-02 主人指出：Studio 有第三档「混合（散文优化框）」，bot 侧一直没接。
    if str(prose or "").strip():
        # Caption 散文：Studio hybrid 流程——动作/姿态照这段写，坐标只按图上看
        user += (U_SEP + prose.strip())
    if focus:
        user += ("\n\n【用户的画图要求，以这个为准】%s\n"
                 "参考图只是一份「主体长什么样」的参考，**最终画面按用户要求来**。"
                 "`summary.caption` 也必须写**要画的那幅画面**，"
                 "**不许写「一张截图 / 一个聊天界面 / 一个手机屏幕」这类描述**——"
                 "截图只是参考图的载体，不是要画的内容。" % focus)
    raw = vision_call(image_path, sys_prompt, user)
    parsed = extract_json(raw)
    norm = RB.normalize_reverse(parsed, None, None)
    if not norm.get("items"):
        raise RuntimeError("视觉模型没给出可用的定位框：%s" % (raw or "")[:200])
    draft = RB.items_to_draft(norm, w, h)
    return LC.sanitize_draft(draft, mode="layout", width=w, height=h)


def compile_draft(draft, cfg=None):
    """草稿 → Moss 提示词（JSON 字符串）。返回 (prompt, error)。"""
    LC, _ = _load_modules(cfg)
    return LC.compile_safe(draft)


def checks(draft, cfg=None):
    """自检：列出会导致编译失败或明显不对的问题（不改草稿）。"""
    try:
        LC, _ = _load_modules(cfg)
        return LC.compile_checks(draft)
    except Exception:
        return []


EDIT_SYS = """你是 MOSS-Image 的布局改稿助手。用户给一份**现有画面的元素清单**和一句修改要求，
你**只输出要改的那几处**（补丁），不要输出整份布局。

输出格式（严格 JSON，不要 Markdown、不要解释）：
{"ops":[
  {"op":"set","index":2,"caption":"改完之后的英文描述"},
  {"op":"add","label":"object","caption":"新元素的英文描述","bbox":[x1,y1,x2,y2]},
  {"op":"remove","index":5}
],"summary":"可选：只有整体构图真的变了才给"}

规则：
1. **没被要求改的元素一律不要出现在 ops 里** —— 它们会自动原样保留，这是「改图」而不是「重画」。
2. `index` 是清单里的下标（从 0 开始），别搞错。
3. caption 一律写**英文**，把中文要求落成英文描述；只描述这次改动涉及的那部分。
4. `add` 的 bbox 用画布左上角起的绝对像素 [x1,y1,x2,y2]，且要在画布内。
5. 只改要求里明确提到的东西；别顺手"优化"别的细节。
6. 如果要求只是整体微调（比如"整体色调冷一点"），给 summary 即可，ops 可以是空数组。"""


def _strip_name(d):
    """喂给模型前把无关字段去掉（省 token）。"""
    keep = ("label", "caption", "bbox", "text", "tags", "palette")
    return [{k: v for k, v in it.items() if k in keep} for it in (d.get("items") or [])]


def edit_draft(draft, instruction, llm_call, cfg=None):
    """在**已有布局**上按一句话改稿，返回 (新草稿, 改了几处)。

    2026-10-25 主人要「像截图里那样改图」，且明确**只走云端 API、不走本地工作流**。
    查过：Moss 云端 API 没有图生图（`/v1/images/edits` 404、`generations` 不认 image 字段），
    Studio 那个聚合端点里的图片模型全在 cline 通道、而 cline 明确"不支持图片输入"。
    所以走 Moss 自己的强项 —— **布局条件生成**：
      反推出布局 → 只改受影响的框 → 其余 caption 原样 → 重新编译 → 出图。

    ⚠️ 补丁模式（不是整份重写）的两个原因：
      ① 整份 JSON 太长会被 max_tokens 截断（实测 10 个框时直接 JSON 解析失败）；
      ② 「没提到的框原样保留」由**代码**保证，而不是指望模型照抄 —— 模型抄漏一个字符画面就跑了。
    """
    LC, _ = _load_modules(cfg)
    base = dict(draft or {})
    items = base.get("items") or []
    if not items:
        raise RuntimeError("没有可改的布局（items 为空）")
    sm = base.get("summary") or {}
    listing = "\n".join(
        "%d. [%s] %s" % (i, it.get("label") or "object", (it.get("caption") or "")[:160])
        for i, it in enumerate(items))
    user = ("画面整体：%s\n\n现有元素（下标. [类型] 描述）：\n%s\n\n修改要求：%s\n\n"
            "只输出 {\"ops\":[...]} 这种补丁 JSON。"
            % (str(sm.get("caption") or "")[:400], listing, instruction))
    raw = llm_call([{"role": "system", "content": EDIT_SYS},
                    {"role": "user", "content": user}])
    data = extract_json(raw)
    ops = data.get("ops")
    if not isinstance(ops, list):
        # 模型没按补丁来、直接吐了整份布局 → 退回整份替换（老逻辑，能用就用）
        if isinstance(data.get("items"), list) and data["items"]:
            data.setdefault("width", base.get("width"))
            data.setdefault("height", base.get("height"))
            data["width"], data["height"] = base.get("width"), base.get("height")
            data.setdefault("bbox_coord_space", "abs")
            data.setdefault("bbox_format", "xyxy")
            return (LC.sanitize_draft(data, mode="layout", width=base.get("width"),
                                      height=base.get("height")),
                    max(1, abs(len(data["items"]) - len(items))))
        raise RuntimeError("改稿结果里没有 ops 也没有 items")

    new_items = [dict(it) for it in items]
    changed = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").strip().lower()
        if kind in ("set", "update", "modify", "change"):
            i = op.get("index")
            cap = str(op.get("caption") or "").strip()
            if isinstance(i, int) and 0 <= i < len(new_items) and cap:
                new_items[i] = dict(new_items[i])
                new_items[i]["caption"] = cap
                if op.get("bbox"):
                    new_items[i]["bbox"] = op["bbox"]
                changed += 1
        elif kind in ("add", "insert"):
            bbox = op.get("bbox")
            cap = str(op.get("caption") or "").strip()
            if isinstance(bbox, list) and len(bbox) == 4 and cap:
                new_items.append({"label": op.get("label") or "object",
                                  "caption": cap, "bbox": bbox})
                changed += 1
        elif kind in ("remove", "delete", "del"):
            i = op.get("index")
            if isinstance(i, int) and 0 <= i < len(new_items):
                new_items.pop(i)
                changed += 1
    if not new_items:
        raise RuntimeError("改完之后一个元素都不剩了，太狠了，没执行")
    out = dict(base)
    out["items"] = new_items
    if str(data.get("summary") or "").strip():
        sm2 = dict(sm)
        sm2["caption"] = str(data["summary"]).strip()
        out["summary"] = sm2
        changed += 1
    return LC.sanitize_draft(out, mode="layout", width=base.get("width"),
                             height=base.get("height")), changed


def summarize(draft):
    """给群里看的短摘要：整体 + 每个框的名字/类型/位置。"""
    sm = (draft or {}).get("summary") or {}
    cap = str(sm.get("caption") or "").strip()
    items = (draft or {}).get("items") or []
    lines = []
    if cap:
        lines.append(cap)
    for it in items[:8]:
        b = it.get("bbox") or [0, 0, 0, 0]
        lines.append("· %s %s @(%d,%d)-(%d,%d)" % (
            it.get("label") or "object", it.get("name") or "", b[0], b[1], b[2], b[3]))
    if len(items) > 8:
        lines.append("…共 %d 个框" % len(items))
    return "\n".join(lines)


def last_note():
    """studio_dir() 最近一次降级/候选择位的说明（2026-11-05 U11：原来挂在函数对象的
    `studio_dir.last_note` 属性上当传出通道，bot 侧要 getattr 才读得到、拼错静默吞空；
    改成模块级状态 + 显式访问函数）。"""
    return globals().get("_LAST_NOTE", "")
