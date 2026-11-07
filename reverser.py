"""反推提示词：一张图 -> 可直接喂绘画工作流的提示词。纯标准库，无第三方依赖。

两条引擎（run() 统一入口）：
- "vl"   : 视觉大模型出英文提示词（默认）。走 config `ai.providers.<reverse.provider>`
           指定的 OpenAI 兼容后端（默认 zhipu 智谱免费档），模型用 `reverse.vl_model`
           （默认 glm-4v-flash）。**多个免费视觉档自动接力**：主档报错/被限流/被下架
           就自动退到 VL_CHAIN 里的下一档，全挂了才报错。
- "wd14" : 本机 ComfyUI 的 WD14-Tagger API 出 danbooru tags（零额度，
           需要 ComfyUI 在线；图会短暂拷进 ComfyUI input/，打完即删）。

⚠️ 2026-09-15 事故：魔搭把 Qwen/Qwen3-VL-235B-A22B-Instruct 下了（HTTP 400
   "has no provider supported"），单点依赖导致反推整体失效 —— 从此改接力链。
   VL_CHAIN 里每一档都得是**免费**的；换模型前先用 state/_probe_vl_models.py
   之类的脚本在真图上验过「真能看图」，别只看 model 列表里有。
   （魔搭那次教训：列表里 36 个模型，绝大多数返回 choices:null，根本没有真实供给。）
⚠️ 2026-09-14 血案教训：系统提示词里绝不放具体角色名/系列名/画师名当"示例"——
小模型会把示范值原样抄成答案。要讲格式就用结构性描述。
⚠️ 2026-09-16 本地 Ollama 大坑（排查了整整一轮，别再走弯路）：
   症状 = 模型**永远只输出思考段、正文空**（`content=''`、`finish=length`、字数恰好撞 max_tokens），
   哪怕问它"用一句话介绍你自己"这种纯文字、不带的图问题也一样，`think:false` 也关不掉。
   **误判**：会看成"模型太弱/预算不够/Ollama 版本不兼容 Qwen3-VL 思考型" —— 全错。
   **真因**：本机 Ollama 的**模型运行层被搞坏了**（反复 `ollama stop` + `taskkill` llama-server
   + 热重启服务这些折腾之后出现）。`ollama stop` 救不回，**重拉模型立刻恢复正常**
   （`ollama pull <同名>` 只补 manifest/校验，几秒完成，不用删旧的）。
   判据：给足预算（如 2000）后若 `content` 仍为空且 `finish=length`，先怀疑运行层，别改代码。
"""
import base64
import json
import re
import mimetypes
import shutil
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
from pathlib import Path

# 本模块之前没有 logger，但新加的工作流反推需要记录（过滤掉什么、工作流读不到等）。
# 单独挂一个 handler，避免污染 root；bot 主模块另有自己的日志配置。
import logging  # noqa: E402

log = logging.getLogger("qqbot.reverser")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

# 默认主档（链首）。⚠️ 2026-10-07 换成 deepseek-flash：实测 1.3~3.6s 认对且质量最高，
#   比智谱 glm-4.6v-flash（8.4s，且免费档高峰期常 429 限流）更稳。
#   `glm-4v-flash` 保留为可选（敏感图会被 400 拦，别当主档）。
VL_PROVIDER_DEFAULT = "modelscope"
VL_MODEL_DEFAULT = "nex-agi/Nex-N2.5-Pro"

# 免费视觉档的接力链：(后端名, 模型名)。模型名 None = 用该 provider 自己配的 model。
# 排序＝又快又稳的在前。**每一条都必须在真图上验过真能看图**（见模块开头注释）。
#
# ⚠️ 2026-10-07 重排（实测依据，见 SEE_DEFAULTS 里的对照表）：
#   原顺序把 `modelscope nex-agi/Nex-N2.5-Pro` 放在第 3 位，实测它是**唯一认对的**
#   （Nahida/Genshin Impact 0.98），却被智谱那两档挡在前面；而智谱对这张图一档
#   内容审核 400、一档限流 429 ⇒ 实际效果是「最好的排在后面白等」。
#   调成：glm-4.6v-flash（更细，能出就用）→ 魔搭（能看图、不受审核）→ glm-4v-flash
#   （最快但对敏感图会 400）→ 思考档 → 本地兜底。
#   ⚠️ 别把 glm-4v-flash 排太后：它对**普通图**是最快的（3~4s），只是敏感图会被拦。
#
# ⚠️ 2026-10-07 **最终重排（第二次纠正，以这张祥子图为准）**：
#   上一版我把 deepseek-flash 提到链首，依据是它 3.6s 认对「初音未来」——
#   **那是个假象**：初音太好认（标志性蓝双马尾+领带），换一张图就原形毕露。
#   用同一张**丰川祥子**图严格复测（每档 2 次，提示词含「绝对不许猜」）：
#     · modelscope nex-agi/Nex-N2.5-Pro → ✅ **Sakiko Togawa / BanG Dream! It's MyGO!!!!!
#       conf 0.96（唯一答对的，与真人对得上）**
#     · deepseek-flash → 2/2 次 known=false（诚实，但**认不出动漫角色**）
#     · glm-4.6v-flash → 2/2 次限流挂
#     · glm-4v-flash → 2/2 次 known=false（诚实，但认不出）
#     · ollama qwen3-vl-4b → ❌ 报 Kogami / LoveLive! 0.95（**自信乱认**，另测还报过
#        山田凉 / Atatier / 雪之下雪乃 / 八奈见杏菜 —— 同一张图 4 个全错的答案）
#   ⇒ 认人**能力**的排序：**魔搭 >> 其余**。DeepSeek 快且诚实，适合当"先探一下"，
#     但它答不出角色名时链会继续往下走，所以放链首不吃亏（0.9~1.5s 就退位）。
#     魔搭放第二 = 真正的认人主力；智谱系排后面（对动漫图常 400/429）。
#   ⚠️ 本地档必须排**最后**：它认不出时会**自信乱报**，而 require_valid 只能挡住
#     known=false，挡不住「0.95 信心的错名字」⇒ 只能在云端全挂时才轮到它。
#   ⚠️ 2026-10-07 **本地档已剔除**（主人定）：它认人时会**自信报错名**
#     （同一张祥子图报过 Kogami / 山田凉 / **Atatier**（把水印昵称当人名）/ 雪之下雪乃 /
#      八奈见杏菜，信心全在 0.85~0.95），而 require_valid 只挡得住 known=false，
#     **挡不住「高信心的错名字」** ⇒ 它留在链里只会污染结果，宁可少一档也不要脏答案。
#     本地模型的价值在「不出网 + 描述画面/反推」，那两条路各有自己的配置，不受影响。
#     ⚠️ 认人有 require_lookup 复核兜底，但本地档常常**连复核都能骗过**（资料库会给
#     任何像样的名字编出设定），所以不能靠复核兜、必须直接移除。
VL_CHAIN = (
    # 🔴 2026-10-29 23:5x 链首换人：原链首 `trae/multimodal_image_kimi-k2.5` **已被站方下架**。
    #   18900 聚合站当天改版（模型数 653 → 87），kimi 视觉档直接 404 ⇒
    #   `describe()`（画同款的反推链）报「视觉模型全用不了」，画同款功能实际已死。
    #   新链首 = `minimax/MiniMax-M3`，按**生产预算（VL_CLOUD_MAX_TOKENS=500）**实测：
    #     · 反推 danbooru tag 3.3s，纯英文零汉字
    #     · 中文描述画面 2.9s（「手持喷火爆竹」「金色锦鲤/铜钱」都对）
    #     · 色块判据（橙方+绿圆）2.6s / 3.0s **两次全对** ⇒ 真吃图
    #   ⚠️ 站方还会再改版，所以**别只依赖链首**：下面的备用档要保住一两个活的。
    #   ⚠️ 待恢复：kimi 若重新上架，可以把它挪回链首（名字整串 `ofm/trae/multimodal_image_kimi-k2.5`）。
    ("custom", "minimax/MiniMax-M3"),        # ✅ **现链首**：3s 反推/2.9s 描述/色块全对
    ("custom", "minimax/MiniMax-M3.1-Flash"),  # 同家备选（13.9s 慢，但确认能吃图）
    ("deepseek", "deepseek-flash"),          # 快 + 诚实（~1s，认不出就说认不出；送图已关思考）
    ("modelscope", "nex-agi/Nex-N2.5-Pro"),  # 旧认人主力（13~19s，2026-10-29 已 429 余额不足）
    ("zhipu", "glm-4.6v-flash"),             # 智谱免费档，看得最细（9~18s，偶发限流）
    ("zhipu", "glm-4v-flash"),               # 智谱免费档，最快 3~4s，但敏感图会被 400 拦
    ("zhipu", "glm-4.1v-thinking-flash"),    # 智谱免费档，带思考段（_strip_think 会剥掉）
    # 链尾本地兜底（2026-10-25 补回来，见下）。
    # 具体模型名由 `_candidates()` 用 `vl_local_model()` / `see_local_model()` 填，
    # 这里写 None 只是占位 —— 「本地优先/只本地」预设也靠这个位置认它。
    ("ollama", None),
)
# 🔴 关于链尾这个 ollama 档（两次改动，别再来回翻）：
#   2026-10-07 移除 —— 当时本地档是 `qwen3-vl-abliterated:4b`（**思考型**）：
#     思考段吃光 token 预算 ⇒ 正文空 ⇒ `finish=length`，一挂稳定 43 秒；
#     而且它会**自信乱认人**（报错名字还给 0.85 信心）。
#   2026-10-25 补回链尾 —— 两个前提都变了：
#     ① 本地档已换成 `qwen2.5-vl-abliterated:7b`（非思考型，12~45s 能出正文）；
#     ② 聊天认人走 `require_lookup=True`，本地报的错名字查不到资料库就被判「没认出」，
#        乱认的代价从「群里说错话」降到「多花 20 秒」。
#   而且不补回来会真的**裸奔**：`see.chain` 预设名一直写着「默认（快→准→**本地兜底**）」，
#   但链里没有本地档 ⇒ 云端全挂就直接抛 RevError。2026-10-25 实测正是这状态：
#   硅基 402 / DeepSeek 402 / 智谱 401，全场只剩魔搭一档，魔搭一抖就整张图哑火。
#   ⚠️ 只放**链尾**：它是兜底，绝不抢首位（`see.local_first` 才管抢位）。

WD14_THRESHOLD_NOTE = 0.35   # 仅文档用；WD14 API 服务端自己按默认阈值出 tag
COMFY_BASE_DEFAULT = "http://127.0.0.1:8188"


def vl_local_model(cfg):
    """本地优先那档用哪个 Ollama 视觉模型：reverse.vl_local_model > ai.providers.ollama.model。

    ⚠️ 2026-09-18 换档：**必须用非思考型**。原来是 `qwen3-vl-abliterated:4b`，
    它是思考型（Qwen3 系），真图反推时思考段会把整个 token 预算吃光，
    `finish=length` + 正文空，给多少预算都一样（2000 照烧、6000 也烧）——
    本地档等于一直是废的，实测同一张图 **43.2s 稳定失败**。
    换成 `qwen2.5-vl-abliterated:7b`（非思考型）后同一张图 **12.7s 成功**；
    显存紧就用 `:3b`（2.98G / 9.6s，描述略糙）。实测对照见
    `_probe_vl_models2.out.txt`。挑本地档时的判据：**别选 Qwen3 系**。
    """
    r = cfg.get("reverse") or {}
    m = str(r.get("vl_local_model") or "").strip()
    if m:
        return m
    provs = (cfg.get("ai") or {}).get("providers") or {}
    return str((provs.get("ollama") or {}).get("model") or "").strip()


def vl_local_first(cfg):
    """本地 Ollama 视觉档要不要排第一（2026-09-16 主人定：本地优先，云端兜底）。"""
    return (cfg.get("reverse") or {}).get("vl_local_first", True) is not False


# ── 聊天识图（看一张图回答「这是什么/是谁」）的独立配置 ────────────────────────
# ⚠️ 2026-10-07 主人反馈「bot 的识图功能还是很差，而且网页里没有选看图模型的功能」。
#    根因（实测确认，见下方 _SEE 的说明）：聊天识图此前**借用反推那条 VL 接力链**，
#    而那条链「本地优先」且首档写死本地 Ollama —— 实测这张图：
#      · ollama qwen2.5-vl-abliterated:7b  14.0s  known:false（认不出）← 却是首档
#      · ollama qwen2.5-vl-abliterated:3b  11.8s  认成「艾琳/原神」（错，图是纳西妲）
#      · zhipu glm-4v-flash                 2.2s  HTTP 400 内容审核拦截（敏感图必挂）
#      · zhipu glm-4.6v-flash               0.5s  HTTP 429 常限流
#      · modelscope nex-agi/Nex-N2.5-Pro   12.1s  Nahida/Genshin Impact 0.98 ✓ 唯一对的
#    却排在链尾第 4 位 —— 前面全挂才轮到它。所以「识图差」不是模型不够多，
#    是**排序把差的排前面、好的排后面，而且主人改不了**。
#    ⇒ 拆出 see.* 一组独立配置：识图不再跟着反推走，网页上可单独选后端/模型/是否本地优先。
#    默认值全部 `None` = 回落旧的 `reverse.*`（保持老行为不变，避免静默突变）。
SEE_DEFAULTS = {
    "provider": None,     # 首选后端；None = 用 reverse.provider / zhipu
    "model": None,        # 首选模型；None = 用 reverse.vl_model / glm-4v-flash
    "local_first": None,  # 本地优先；None = 用 reverse.vl_local_first
    "local_model": None,  # 本地档模型；None = 用 reverse.vl_local_model
    "chain": None,        # 自定义接力链（列表）；None = 用内置 VL_CHAIN
    "follow_brain": None, # 识图要不要直接跟着「模型选择」走；None/False = 各管各的
}


# ============ 识图跟随聊天大脑（2026-10-30 主人要求）============
# 主人原话：「聊天和识图用一个模型更准确、而且省 token」「你不要挑，我自己选」。
# 语义：`see.follow_brain=true` ⇒ **识图直接读 `ai.providers.<大脑>.model`**，
#   主人只在「模型选择」挑一次，聊天与识图共用同一个模型，不用维护两份配置。
#
# ⚠️ 为什么放在**解析层**（see_provider/see_model）而不是改各调用点：
#   识图有两条链（`describe_scene()` 群聊看图、`describe()` 画同款反推）外加
#   `answer_image()` 看图问答、`appearance()` 认人外观 —— 调用点多达四五处，
#   逐个改必漏（10-29 就是只改了 see.model、画同款照样挂）。在解析层一处生效，
#   所有走 see_* 的链路**自动跟随**，零漏点。
#
# ⚠️ 两条硬边界（必须清楚，否则会以为开关坏了）：
#   ① 只有**云端 API 档**（openai 协议、能收 image_url）才可能真看图；
#      qoder / dsh / agent 这些大脑本身不收图 ⇒ 跟随它们等于识图必挂，
#      所以 `follow_brain` 只认 `ai.providers` 里有 url 的云端后端。
#   ② 跟随的是**模型名**，不是「后端类型」。大脑若是 ollama 本地档，
#      跟随它也走本地 ollama（不联网、不审查），这是**特性不是 bug**。

def _brain_name_of(cfg):
    """当前聊天大脑是哪一档（`ai.provider`）。兼容 private_provider。"""
    ai = cfg.get("ai") or {}
    return str(ai.get("provider") or "").strip()


def follow_brain_on(cfg):
    """识图是否跟随聊天模型（默认关，零回归）。"""
    s = see_cfg(cfg)
    v = s.get("follow_brain")
    return bool(v) if v not in (None, "") else False


def brain_vl_target(cfg):
    """取聊天大脑那档的 (后端, 模型)，供识图跟随用。

    返回 (provider, model)；拿不到（大脑档不存在/没 url）就返回 (None, None)，
    调用方据此回落原来的 see.* 行为（**绝不让开关把识图搞成完全没档可用**）。
    """
    name = _brain_name_of(cfg)
    if not name:
        return None, None
    pcfg = (cfg.get("ai") or {}).get("providers") or {}
    p = pcfg.get(name) or {}
    if not (p.get("url") or p.get("base_url")):
        return None, None
    m = str(p.get("model") or "").strip()
    if not m:
        return None, None
    return name, m


def see_cfg(cfg):
    """取聊天识图配置（see.*），未设的键回落到 reverse.* 的老值。"""
    out = dict(SEE_DEFAULTS)
    see = cfg.get("see") or {}
    for k in SEE_DEFAULTS:
        if see.get(k) not in (None, ""):
            out[k] = see[k]
    return out


def see_provider(cfg):
    s = see_cfg(cfg)
    # 2026-10-30：跟随聊天大脑（主人只挑一次）。拿得到大脑档就**优先用它**，
    # 拿不到就原样回落，绝不因为开关把识图搞成没档可用。
    if follow_brain_on(cfg):
        _bp, _bm = brain_vl_target(cfg)
        if _bp:
            return _bp
    p = s["provider"]
    if not p:
        # 「识图模型」写成一整串「后端/模型」时，后端也从这里切出来。
        # ⚠️ 2026-10-29：模型名自身可能带斜杠（`ofm/trae/multimodal_image_kimi-k2.5`
        #   里的 `trae/...`），所以**不能按第一个 `/` 切** —— 按 `providers` 里真实
        #   存在的后端名做前缀匹配，切不中就整体当模型名（回落后端默认）。
        m = str(s["model"] or "")
        if "/" in m:
            head = m.split("/", 1)[0].strip()
            provs = (cfg.get("ai") or {}).get("providers") or {}
            if head in provs:
                p = head
    return p or (cfg.get("reverse") or {}).get("provider") or VL_PROVIDER_DEFAULT


def see_model(cfg):
    s = see_cfg(cfg)
    # 2026-10-30：跟随聊天大脑 —— 模型名直接取 `ai.providers.<大脑>.model`。
    if follow_brain_on(cfg):
        _bp, _bm = brain_vl_target(cfg)
        if _bm:
            return _bm
    m = str(s["model"] or "").strip()
    # 「后端/模型」串：模型部分去掉后端前缀（同上，仅当前缀是真实存在的后端名）。
    if m and "/" in m:
        head = m.split("/", 1)[0].strip()
        provs = (cfg.get("ai") or {}).get("providers") or {}
        if head in provs:
            m = m.split("/", 1)[1].strip()
    return m or (cfg.get("reverse") or {}).get("vl_model") or VL_MODEL_DEFAULT


def see_local_first(cfg):
    """识图要不要本地优先。

    ⚠️ 2026-10-07 默认改成 **False**（和反推相反）。理由（实测）：
      本地 qwen2.5-vl-7b/3b 认人**要么认不出、要么自信认错**，还慢（12~14s，实测
      有一次跑了 98 秒）；而云端魔搭那档又快又准。让本地排第一 = 每张图先白等十几秒
      再走云端，主人看到的「识图很差很慢」就是这么来的。
      ⇒ 识图默认「云端优先、本地只当最后一档兜底」；想看敏感图/要完全离线，
        在网页「识图接力链」里选「本地优先」或「只本地」即可。

    优先级：`see.local_first`（显式布尔）> `see.chain` 预设名 > `reverse.vl_local_first`。
    """
    s = see_cfg(cfg)
    preset = str(s["chain"] or "").strip()
    if preset.startswith("本地优先") or preset.startswith("只本地"):
        return True
    if preset.startswith("只用云端"):
        return False
    v = s["local_first"]
    if v is None:
        # 没显式设过：识图**默认不本地优先**（见上），不再是沿用反推的 True
        return False
    return v is not False


def _strip_note(v):
    """把「模型名　—　标注」还原成纯模型名。

    ⚠️ 2026-10-07：网页「识图本地模型」下拉框为了给主人看效果标注，
    候选值里带了中文说明（如 `qwen3-vl-abliterated:4b　—　认人最准（13~38s）`）。
    送 Ollama 前**必须剥掉**，否则会 404 model not found。前端零改动，在这里收口。"""
    s = str(v or "").strip()
    for sep in ("　—　", " — ", "  —  ", " | "):
        if sep in s:
            return s.split(sep)[0].strip()
    return s


def see_local_model(cfg):
    """识图用的本地 Ollama 模型。没单独设时沿用 reverse.vl_local_model。

    ⚠️ 剥掉网页下拉里带的「— 效果标注」后缀（见 _strip_note）。"""
    m = _strip_note(see_cfg(cfg)["local_model"])
    return m or vl_local_model(cfg)


def see_chain(cfg):
    """识图接力链：see.chain 优先（主人自定义），否则用内置 VL_CHAIN。

    ⚠️ 2026-10-07：`see.chain` 支持**预设名**（网页下拉给的是这几个中文名），
    不是非要写「后端/模型」串 —— 主人不该为了换顺序手打一长串模型名。
      · 默认/空            → VL_CHAIN（快→准→本地兜底）
      · 本地优先           → 内置链但把本地档挪到最前（靠 see_local_first 生效）
      · 只用云端           → 砍掉链尾的 ollama 档，绝不碰本地显存
      · 只本地             → 只剩 ollama 一档，完全不出网
    """
    c = see_cfg(cfg)["chain"]
    if isinstance(c, str):
        s = c.strip()
        if not s or s.startswith("默认"):
            return VL_CHAIN
        if s.startswith("只用云端") or s.startswith("不占本地"):
            return tuple(x for x in VL_CHAIN if x[0] != "ollama")
        if s.startswith("只本地") or s.startswith("不联网"):
            return (("ollama", None),)
        # 「本地优先」= 链不变，靠 local_first 把本地挪前面（见 see_local_first）
        if s.startswith("本地"):
            return VL_CHAIN
        # 也允许直接写「后端/模型,后端/模型」
        c = [x for x in s.replace("，", ",").split(",") if x.strip()]
    if isinstance(c, (list, tuple)) and c:
        out = []
        for it in c:
            if isinstance(it, (list, tuple)) and len(it) == 2:
                out.append((str(it[0]), it[1]))
            elif isinstance(it, str) and "/" in it:
                a, b = it.split("/", 1)
                out.append((a.strip(), b.strip() or None))
        if out:
            return tuple(out)
    return VL_CHAIN


def see_chain_preset(cfg):
    """`see.chain` 是不是预设名（预设名时「本地优先」要额外把本地挪前）。"""
    c = see_cfg(cfg)["chain"]
    return str(c).strip() if isinstance(c, str) else ""


def see_keep_alive(cfg):
    """本地识图模型用完在显存留多少秒（see.keep_alive，缺省跟反推走）。"""
    see = cfg.get("see") or {}
    v = see.get("keep_alive")
    if v in (None, ""):
        return int((cfg.get("reverse") or {}).get("vl_local_keep_alive") or 0)
    try:
        return int(v)
    except Exception:
        return 0


# 本地那档的 token 预算要单独给大 —— 它是个**思考型**模型（Qwen3-VL 魔改），
# 思考段会先吃掉一大截 token，给 500 的话 `content` 永远是空串
# （2026-09-16 实测：finish_reason=length、content=''、972 字全在 reasoning 里）。
# 实测 1500~2000 才够它想完再答；500 预算下本地档等于废的。
# ⚠️ 但 2026-09-16 深度实测后**下调到 1200**：这只 4b 是 abliterated（去审查）变体，
# 真图反推时思考段会无限发散，给 2000 吐 4279 字、给 6000 吐 7008 字，**永远是
# finish=length + 正文空**，加预算只是多等 30 秒还失败 → 不如把预算压小 + 快速失败，
# 把时间让给云端（智谱 3~4s）。见 VL_LOCAL_BREAKER_* 熔断。
VL_LOCAL_MAX_TOKENS = 1200
VL_CLOUD_MAX_TOKENS = 500

# 本地档**熔断**（2026-09-16 加）：本机这只 4b 稳定性实测只有 ~1/3（真图反推），
# 失败一次要白等 20~40 秒。连续失败 BREAKER_N 次就静默 BREAKER_SEC 秒，期间不再试本地、
# 直接走云端 —— 免得群友每张图都先陪它熬 30 秒。成功后立刻恢复正常优先级。
# 2026-09-18 补：熔断**只在本地档真的不可用时**才该触发。换掉思考型 4b（见 vl_local_model）
# 之后本地档才第一次真正可用 —— 在那之前「本地优先」形同虚设，等于每张图都先白等 30 秒
# 再让云端兜底。所以：本地档失败率异常高时，先查是不是又换回了思考型模型。
VL_LOCAL_BREAKER_N = 2
VL_LOCAL_BREAKER_SEC = 600
_vl_local_fail = [0, 0.0]        # [连续失败次数, 静默到哪个时间戳]


def vl_local_ok_to_try(now=None):
    """本地档现在是否允许尝试（熔断期返回 False）。"""
    now = now if now is not None else time.time()
    return now >= _vl_local_fail[1]


def _vl_local_note(ok):
    """记一次本地档的成败，维护熔断窗口。"""
    if ok:
        _vl_local_fail[0], _vl_local_fail[1] = 0, 0.0
        return
    _vl_local_fail[0] += 1
    if _vl_local_fail[0] >= VL_LOCAL_BREAKER_N:
        _vl_local_fail[1] = time.time() + VL_LOCAL_BREAKER_SEC


# 本地 Ollama 档**必须**带这两个惩罚，否则小模型会绕圈复读：
# 实测同一张图、同一提示词（qwen2.5-vl-3b，2026-09-16 试的那款），无惩罚 = 17.3s / 1686 字，
# 后半段全是 "shiny lips, shiny hair, shiny droplets, shiny clothing, shiny skin…"
# 无限循环（finish=length）；加上 frequency_penalty=0.8 + presence_penalty=0.4 =
# **1.2s / 155 字 / finish=stop**，干净一句话。现在主力仍是 4b 思考型，这俩惩罚对它没坏处。
# （云端档不加：各家接口对这两个参数支持不一。
#   Ollama 的 /v1 兼容层会把 frequency_penalty 映射成内部 repeat_penalty。）
OLLAMA_VL_PENALTY = {"frequency_penalty": 0.8, "presence_penalty": 0.4}


def vl_max_tokens(cfg, pname, p):
    """这次调用给多少 token：档位自己写了 max_tokens 就用它，
    否则本地（ollama 型）给 VL_LOCAL_MAX_TOKENS，云端给 VL_CLOUD_MAX_TOKENS。"""
    try:
        v = int((p or {}).get("max_tokens") or 0)
    except Exception:
        v = 0
    if v > 0:
        return v
    r = cfg.get("reverse") or {}
    if str(pname) == "ollama" or str((p or {}).get("type") or "").lower() == "ollama":
        try:
            v = int(r.get("vl_local_max_tokens") or 0)
        except Exception:
            v = 0
        return v if v > 0 else VL_LOCAL_MAX_TOKENS
    return VL_CLOUD_MAX_TOKENS


def _vl_url(p):
    """视觉接口地址：Ollama 必须走 OpenAI 兼容的 `/v1` 前缀。
    ⚠️ 血案：config 里 ollama.url 写的是裸端口（http://127.0.0.1:11434），
    直接拼 /chat/completions 会 404 —— 本地那档等于一直没生效（2026-09-16 发现）。"""
    import re as _re
    url = str(p.get("url") or "").rstrip("/")
    if str(p.get("type") or "").lower() == "ollama" and not _re.search(r"/v\d+$", url):
        return url + "/v1"
    return url


def _ollama_keep(url, model, sec=0):
    """跟 Ollama 打个招呼：这个模型留在显存里 sec 秒（0 = 立刻卸）。
    不这么做的话 3G 显存会被它占着，跟出图抢（8G 卡尤其明显）。失败当没事。

    ⚠️ `keep_alive=0` 会**先把模型加载起来再卸掉**（实测白花 5~8 秒）。
    所以 sec<=0 时先查 /api/ps：没挂着就直接返回，省掉这一趟无用装卸。"""
    if not url or not model:
        return
    base = str(url).rstrip("/")
    if int(sec) <= 0:
        try:
            with urllib.request.urlopen(base + "/api/ps", timeout=5) as r:
                loaded = [str(m.get("name") or m.get("model") or "")
                          for m in (json.loads(r.read().decode()).get("models") or [])]
            if not any(model == n or n.startswith(model.split(":")[0]) for n in loaded):
                return
        except Exception:
            pass
    try:
        _post_json(base + "/api/generate",
                   {"model": model, "keep_alive": int(sec), "prompt": ""}, {}, 8)
    except Exception:
        pass


# vl 引擎的系统提示词。⚠️ 只用结构性描述，不举任何具体例子（防"示范值被照抄"）。
DESC_SYS = (
    # 身份：把模型当"标注工具 + 摄影/概念美术"，讲客观构造，别让它去评价作品。
    "你是客观的图像标注工具，同时具备电影摄影与概念美术的专业视角。"
    "用户给你一张图，你要通过还原它的视觉构造，把它变成可直接用于 AI 绘画模型的英文提示词。"
    "你要做的是**如实还原**，不是概括、不是评价。"
    "细节越详尽、越精确越好；人物面部特征与表情要写得更有细节、更鲜活。"
    # ⚠️ 格式定调**必须放开头**（2026-10-04 实测）：本地 7b 对靠后弱化的格式指令服从度很差，
    # 会把tag 串写成自然语言长句 ⇒ anima 档拿到长句 = 09-19 血案（krea2 走平涂动漫画风）。
    "**输出格式（最高优先级，先看这条）**：除非图片是照片级写实风，"
    "否则一律输出**逗号分隔的小写短语标签**（例如 1girl, solo, long silver hair, "
    "purple eyes, medium close-up, soft lighting），**不要写成句子、不要用完整句语法**。"
    "只有当图片明显是照片/写实风格时，才改用自然语言长句。"
    "要求："
    # ---- 命名（主人第 3 条）----
    "1) 人物命名：用户提供了名字就用那个名字；没提供就用通用称谓（如 a young woman / a young man）"
    "这类中性表述，**绝不猜测、绝不用占位符**（不要写 unknown、character_1 这类）。"
    # ---- 禁语清单（主人第 4 条，逐条落地）----
    "2) 严禁出现以下写法：总结式评价（如「构图很精妙」）、氛围或情绪词（如温馨/浪漫/可爱/悲伤）、"
    "推测式措辞（如 seems to / as if / 看起来像）、对画师或作品的赞美或批评、"
    "用括号写批注、以及「the picture shows / 图片中展示了」这类冗余开场。"
    "只写看得见的东西。表情与氛围要**通过具体的面部特征与肢体状态**来表达，而不是直接写情绪词。"
    # ---- 主体与内容（主人第 8 条的正向部分）----
    "3) 主体与数量：写清主要对象、主体及数量（人/动物/物体/场景皆可）。"
    "主体不一定是人、也不一定是生物；**哪怕是纯场景也要逐项描述其细节**。"
    "4) 外貌：性别、年龄、体型体态、发型发色、眼型、瞳色、五官面貌 —— 有什么特征写什么特征，"
    "**直接描述看得见的面部特征，不描述种族**；**禁止无端猜测或凭空捏造**。"
    "5) 服装逐件描述：每一件衣物分别说明款式、颜色、长度、材质，以及配饰、装饰部件、"
    "物件材质、纹理、标志、特殊结构、装备武器、具体穿着状态。"
    "**多人时分别写清每个人的动作与微表情，并写明他们之间的互动与空间关系。**"
    # ---- 景别与镜头（主人第 5 条）----
    "6) 景别与机位：写明景别（极特写/特写/中景/全身/远景/大远景）以及画面更接近人像、环境还是电影场景；"
    "描述机位高度、机位位置、观看角度、透视变形，以及镜头感觉（广角/长焦压缩/微距等）。"
    # ---- 构图（主人第 6 条）----
    "7) 构图：主体在画面中的位置，前景/中景/背景的关系，主体与环境的比例关系，"
    "留白，引导线，以及取景框内的框景元素。"
    # ---- 光影与色彩（主人第 7 条）----
    "8) 视觉设计与光影：环境结构、重要形状与剪影、色彩布局、光线方向、明暗对比、"
    "大气效果（雾/霾/体积光）、光源、投影。"
    # ---- 环境与建筑（主人第 9 条）----
    "9) 环境与建筑：详述背景设定、所有物件及其相对位置、建筑风格。"
    # ---- 质感（主人第 10 条，去掉自相矛盾的那半句）----
    "10) 质感与渲染：描述画面的质感、景深、以及任何视觉效果（光斑/辉光/景深虚化等）。"
    # ---- 旧规则保留的硬约束（别丢）----
    "11) 画面里没有的东西不要编，宁可少写；只在有把握时才写。"
    "图里若出现文字标牌，可加表示「画面含文字」的短语（描述文字的位置/语言/风格），"
    "但**绝不把图里的中文字句抄进提示词**。"
    "12) 只输出提示词本身：不要任何解释、前缀、引号、分类标题或 markdown。"
    "**绝不输出中文，全部输出必须是纯英文。**"
    # ---- 输出格式（主人第 13 条的结构要求，格式按主人 11:39 的选择改成 tag）----
    "13) 输出格式（重申上面的定调，别忘了）：**默认就是逗号分隔的小写标签短语**，"
    "形如 1girl, solo, long silver hair, purple eyes, detailed face, medium close-up, "
    "soft lighting —— 这样的**短语堆叠**，不是句子。"
    "**只有照片级写实图**才用自然语言长句。"
    "无论哪种都要保持**逻辑连贯**：人物动作与空间关系说得通、前后因果一致。"
    "直接输出，不要分类标题、不要额外说明、不要 markdown。"
)


# 认人用的系统提示词。⚠️ 同样不举任何具体角色/作品名当例子（防示范值被照抄成答案）。
ID_SYS = (
    "你在辨认一张图里画的是不是某部已有作品里的特定角色。只输出一行 JSON，"
    "不要解释、不要代码块、不要 markdown 围栏：\n"
    '{"known": true 或 false, "name": "角色名", "series": "作品名", "confidence": 0到1的小数}\n'
    "判定规矩："
    "1) 只有确认是某部已有动漫/游戏/影视/虚拟主播作品里的特定角色时才 known=true；"
    "原创角色、路人、真实人物、动物、风景、照片一律 known=false；"
    # ⚠️ 2026-10-07 实测（祥子图 x 15 次）：旧版只说「拿不准就 false」，
    #    模型照样在 0.6 边上随口编名字（同一张图先后报出雪之下雪乃 / 八奈见杏菜 /
    #    Otonose Kanade 三个互不相同的错答案，且信心都报 0.75）。
    #    ⇒ 「认错人比认不出更糟」这句中文模型不吃，必须写成**可执行的硬指令**：
    #      禁止猜 + 抬到 0.8 + 明确"不能靠感觉"。
    "2) **绝对不许猜。** 没十足把握就必须 known=false —— 认错人比认不出严重得多；"
    "confidence 低于 0.8 一律写 false，不要用 0.75 这种「有点像我猜是」的数字凑；"
    "3) 只要你没在真正的作品资料里见过这个角色，就算画面很像某角色也必须 known=false；"
    "4) name 与 series 只用拉丁字母或罗马字书写（用该角色在资料库里最常见的写法），不要写中文；"
    "5) known=false 时 name 和 series 都填空字符串。"
)


# ========== 反推走ComfyUI 工作流（2026-10-04 主人定）==========
# 工作流：ComfyUI/user/default/workflows/图片反推提示词.json
#   链：ZML_TextInput(反推一 英文规则) → ImageCaptionNode(custom_rule_content)
#       ← ZML_TextInput(反推二 中文规则) → ShowText
#   模型 = JoyCaption Q4_K_M（与 bot 原engine=cap 同一个模型，差别只在规则）
#
# 探针实测（tools/_probe_rev_wf.py，真跑，初音图）：**24.6s 出文本**，
#   outputs["4"]["text"][0] 就是结果；wf_to_api(doc, texts=None) 转换 5 节点、node_errors 空。
#
# ⚠️ 三个踩过的坑（别重犯）：
#   1) **必须 wf_to_api(doc, texts=None)** —— 出图路径传 texts 会把 caption 链的
#      STRING 入口内联空串（comfy_gen.py:1919「防残留图污染提示词」），
#      **会把本工作流的规则也清空** ⇒ 必须 texts=None。
#   2) **image 槽要 IMAGE 张量、不是文件名字符串** —— 工作流里image 槽 link=None
#      （主人在界面手接的），bot 跑必须自己加一个 LoadImage。
#      直接塞字符串 ⇒ 服务端 `'str' object has no attribute 'shape'`（实测 execution_error）。
#   3) 这工作流**没有存图节点**（只有 ShowText），取结果走
#      `/history/<pid>` 的 `outputs[ShowText节点id]["text"][0]`。
REV_WF_NAME = "图片反推提示词.json"

# 规则文本：主人工作流里那两份原文的**可用部分**。
# 去掉了：① 移除内容限制/「无限制绝不拒绝」② 敏感情景必填生理词汇+胸部大小
#        ③ 忽略马赛克描述被遮挡区域
# ④ 曾去掉「不要 tag 格式」（2026-10-04 11:39 主人当时选了 tag）——
#    **2026-10-06 主人改口「不用 tag 形式了」⇒ 已在 REV_WF_SYS 里恢复成「连贯自然语言」**。
# 修掉了：自相矛盾处（原文既要求"不描述画风"又要求"综合描述"导致输出 Digital anime illustration），
#        凭空编造处（原文鼓励"不吝啬字数"实测产出图里没有的 breasts/tattoo）。
# 反推结果的「凭空编造」高危词过滤（2026-10-04 加）
# JoyCaption 实测会编造图里不存在的**外观特征**（已放大原图核实肩部无纹样，
# 它仍写 visible tattoo on right shoulder）。prompt 已把「不许编造」提到最前，
# 仍压不住 ⇒ 加一道后处理硬过滤。
# ⚠️ 只砍**外观特征类**：图里没有就是错、有了也是同义反复，砍掉零损失。
#   场景/构图/服装/光影类**不砍** —— 那些可能是真看到的。
REV_HALLUCINATE = re.compile(
    r"\b("
    r"tattoos?|tattooed|"
    r"scars?|scarred|"
    r"moles?|freckles?|"
    r"birthmarks?|"
    r"piercings?|"
    r"binds?|bondage|"
    r"nipples?|areola|"
    r"genitals?|vagina|penis|"
    r"nipple"
    r")\b", re.I)


# 否定词：命中特征词但前面有这些的是"否定表述"（no tattoo / without scar），不该砍
_RE_NEG = re.compile(r"\b(no|not|none|without|free\s+of|lacking)\b", re.I)
# 判定「tag 片段 vs 自然语言」。danbooru tag **不用冠词、也不用 be 动词**：
#   good : 1girl / visible tattoo on right shoulder / tattoo on arm / medium close-up
#   句子  : a young woman with a tattoo / she has a scar / the tattoo is red
# ⚠️ 介词(on/in/with)、动词(marks/shows)、连词在 tag 词组里**合法**，所以不看它们。
# ⚠️ 2026-10-04 三轮实测记录：
#   v1 只看「有逗号」→ 长句被砍；
#   v2 「<=7 词且无 be 动词」→ 6 词长句仍被砍；
#   v3 「>5 词或含冠词/介词就放行」→ 真 tag 反而砍不动了。
#   v4 =只看**冠词 + be 动词**（这两类才是句子专属），词数上限放宽到 8。
_RE_SENT = re.compile(
    r"\b(a|an|the|is|are|was|were|has|have|had|am|be|been|being|"
    r"she|he|it|they|we|you|i)\b"
    r"|[的]", re.I)


def _is_tag_fragment(frag):
    """这段像 danbooru tag 片段（无冠词/无 be 动词/无代词）还是自然语言句子？

    取向：拿不准一律当句子放行 —— 过滤器职责是"不删错"，不是"删得狠"。
    """
    words = frag.split()
    if not words:
        return False
    if len(words) > 8:# 超过 8 词基本是句子
        return False
    if _RE_SENT.search(frag):# 有冠词/be动词/代词 ⇒ 句子
        return False
    return True


# 句首的「媒介/风格」限定词：规则 (b) 明令不许写媒介，但 JoyCaption 这个开头太顺嘴 —
# 实测 2026-10-06 改成自然语言后，输出仍是「A digital painting of a young woman with …」。
_REV_MEDIUM_HEAD = re.compile(
    r"^\s*(?:(?:a|an|the)\s+)?"          # 冠词可有可无（「3d render of …」也是常见开头）
    r"(?:"
    r"digital\s+(?:painting|artwork|art|illustration|image)|"
    r"anime[\s-]+(?:style[\s-]+)?(?:illustration|artwork|art|image)|"
    r"(?:digital\s+)?illustration|painting|artwork|"
    r"ai[\s-]*(?:generated|art)|"
    r"cel[\s-]*shaded\s+(?:illustration|artwork)|"
    r"3d\s+render(?:ing)?|render(?:ing)?"
    r")\s+"
    # ⚠️ 第三人称单数也要收（depicts / shows / features）—— 只写 -ing 会漏
    #    「The digital artwork depicts a cat」，实测漏过。
    r"(?:of|depicting|depicts|showing|shows|featuring|features)\s+", re.I)


def strip_medium_head(text):
    """掐掉**句首**的媒介/风格词：「A digital painting of a young woman …」→「A young woman …」。

    返回 (文本, 是否改过)。

    ⚠️ 只动句首这一个模式。**句子中间的风格词不碰** —— 那得拆句，会破句，
    跟 `strip_hallucinated_tags` 一个取向（宁可不删，也不删错）。
    """
    s = str(text or "")
    m = _REV_MEDIUM_HEAD.match(s)
    if not m:
        return s, False
    rest = s[m.end():].lstrip()
    if not rest:
        return s, False
    # 直接删掉整个头部、正文原样保留（别自作聪明补冠词，会得到「A a young woman」）
    return rest[:1].upper() + rest[1:], True


def strip_hallucinated_tags(text):
    """掐掉凭空编造的外观特征词（tag 串用，逗号分隔）。

    **只动 tag 片段，绝不动自然语言句子**（硬砍会破句）—— 判据见_is_tag_fragment。
    否定表述（no tattoo / without scar）放行。
    拿不准一律放行：宁可不删，也不删错。
    返回 (清理后文本, 被砍掉的词列表)。
    """
    if not text or "," not in str(text):
        return text, []
    parts = [p.strip() for p in str(text).split(",")]
    kept, dropped = [], []
    for p in parts:
        if not p:
            continue
        if REV_HALLUCINATE.search(p):
            # 否定表述放行
            if _RE_NEG.search(p):
                kept.append(p)
                continue
            # 句子片段放行（砍了会破句）
            if not _is_tag_fragment(p):
                kept.append(p)
                continue
            dropped.append(p)      # ← 只砍「短 tag 片段」
        else:
            kept.append(p)
    return ", ".join(kept), dropped


# ---- 画风/媒介词（主人规则 (b) 明令禁止的那些） ----
# 2026-10-24 实测：JoyCaption 是**描述能力最强**的本地档，但它的训练目标就是
# 「连画风一起说」，所以稳定以 "A digital painting of Vincent van Gogh's
# "Starry Night" style." 开头 —— 长规则、短规则都拦不住（23.7s / 31.1s 两次全中）。
# 换模型能听话但描述变糙；正确解法是**让它描述、事后摘掉纯画风句**。
_STYLE_WORDS = re.compile(
    r"\b(digital\s+(painting|art|illustration|artwork)|oil\s+painting|watercolou?r|"
    r"painting|illustration|drawing|sketch|anime|manga|cartoon|cel[\s-]shading|"
    r"photorealistic|photograph|photo|rendered|rendering|concept\s+art|artwork|"
    r"screenshot|in\s+the\s+style\s+of|style)\b", re.I)


def strip_style_lead(text):
    """摘掉开头那种「纯画风」句（不含画面内容的）。

    只摘满足三条的首句：① 词数 ≤ 22 ② 命中画风/媒介词 ③ 后面还有正文。
    这样 "A digital painting of X's Y style." 会被摘掉，
    而 "A young woman stands in the rain, her coat soaked." 这种真实描述不动。
    另外清掉句中残留的 ", in the style of X" 片段。
    """
    t = (text or "").strip()
    if not t:
        return t
    m = re.match(r"^([^.!?]{5,160}[.!?])\s*", t)
    if m:
        first, rest = m.group(1), t[m.end():]
        words = re.findall(r"[A-Za-z']+", first)
        if len(words) <= 22 and _STYLE_WORDS.search(first) and rest.strip():
            # 摘掉后可能留下引号/标点残渣（原句里常有 "The Starry Night." 这种带引号的标题）
            t = rest.strip().lstrip('"\'“”‘’').strip()
    t = re.sub(r",?\s*in\s+the\s+style\s+of\s+[^,.;]+", "", t, flags=re.I)
    return t.strip()


REV_WF_SYS = (
    "You are an objective image annotation tool with the eye of a cinematographer and concept artist."
    "Analyze the image and produce a prompt usable directly by an AI image model."
    "Do the reverse engineering of visual construction; do NOT summarize or evaluate the artwork."
    "Be as detailed and precise as the image allows, and describe facial features and expressions vividly."
    # ⚠️ 两条硬约束放最前面 —— 2026-10-04 实测：写末尾模型就不听
    #（第一次跑输出里混进了 digital painting / anime style / 编造的 tattoo）。
    "**Hard rules, read before anything else (they override any later wording):**"
    " (a) Write ONLY what is actually visible in the image. Never add a body feature, tattoo, scar,"
    " mark, piercing, accessory, garment or detail that you cannot clearly see. **When in doubt,"
    " leave it out — missing detail is far better than invented detail.**"
    " (b) Never name the medium, genre or art style: no 'digital painting', 'anime', 'anime style',"
    " 'illustration', 'screenshot', 'photorealistic', 'art style', 'cel shading', 'rendered in',"
    " 'in the style of'. Describe the visual result (e.g. clean linework, flat colour, soft gradient)"
    " instead of naming the style."
    # ⚠️ 输出格式 2026-10-06 主人改口：**不要 tag 形式了，要连贯自然语言**
    #    （原话「反推提示词不用 tag 形式了，就沿用这个工作流」）。
    #    上一版这里写的是「output a comma-separated list of short lowercase tag phrases ... not
    #    sentences」，因为 2026-10-04 11:39 主人当时选的是 tag；现在改回自然语言。
    #    ⚠️ 改这里必须**同时**看 `strip_hallucinated_tags` 的 `_is_tag_fragment`：
    #    输出变句子后，那个过滤器靠"是不是 tag 片段"来放行，别让它把句子砍破。
    "**Output format (highest priority, read first)**: output **one flowing paragraph of plain"
    " English natural language** — a coherent, logically connected description, written the way a"
    " cinematographer describes a shot. **NOT a tag list, NOT comma-separated keywords, NOT a chain"
    " of short fragments.** Use full sentences and weave subject → appearance → action and expression"
    " → shot and camera → composition → lighting and colour → environment into connected prose."
    "Requirements:"
    "1) Naming: if a character name is given use it; otherwise use neutral wording"
    " such as 'a young woman'; never guess, never use placeholders."
    "2) Never write: summary evaluations ('the composition is exquisite'), atmosphere or emotion words"
    " (warm, romantic, cute, sad), speculative phrasing (seems to, as if), praise or criticism of the"
    " artist, parenthetical comments, or redundant openers such as 'the picture shows'."
    "3) Subject and count. Subject need not be human nor even animate; a pure scene must also be"
    " described in detail."
    "4) Appearance: sex, apparent age, build, hairstyle, hair colour, eye shape, iris colour, facial"
    " features — describe what is visible, do NOT describe race, and do NOT invent anything absent."
    "5) Clothing item by item: style, colour, length, material; plus accessories, trims, object"
    " materials, textures, emblems, special structures, weapons, how items are worn."
    " For multiple characters, describe each one's action and micro-expression separately and state"
    " their interaction and spatial relations."
    "6) Shot and camera: shot type (extreme close-up, close-up, medium shot, full shot, wide shot,"
    " establishing shot), whether it reads as portrait / environment / cinematic scene, camera height,"
    " camera position, viewing angle, perspective distortion, lens feel (wide angle, telephoto"
    " compression, macro)."
    "7) Composition: subject placement, foreground / midground / background relations, subject-to"
    " environment scale, negative space, leading lines, framing elements."
    "8) Lighting and colour: environment structure, important shapes and silhouettes, colour"
    " arrangement, light direction, contrast, atmospheric effects (fog, haze, volumetric light),"
    " light sources, shadows."
    "9) Environment and architecture: setting, all objects and their relative positions,"
    " architectural style."
    "10) Texture and rendering: surface texture, depth of field, visual effects (bokeh, glow)."
    " If the image contains visible text, you may note that text is present, but never copy the"
    " actual characters. Output pure English only, no Chinese, no explanation, no markdown."
    "**Self-check before you answer**: delete anything you cannot see; delete every style or"
    " medium word; delete every invented detail; then output the flowing natural-language description."
)


def _rev_services(cfg):
    """反推工作流要依次尝试的 VLM 服务（"服务名/模型名"）。
    默认：DeepSeek/deepseek-flash 优先，跑挂自动换 Ollama joycaption 补上（2026-10-13 主人定）。
    改法：reverse.wf_vlm_chain（列表，优先级最高）或 reverse.wf_vlm_primary / wf_vlm_fallback。"""
    r = cfg.get("reverse") or {}
    chain = r.get("wf_vlm_chain")
    if isinstance(chain, (list, tuple)) and chain:
        seq = [str(x).strip() for x in chain if str(x).strip()]
    else:
        p = str(r.get("wf_vlm_primary") or "").strip() or "DeepSeek/deepseek-flash"
        f = str(r.get("wf_vlm_fallback") or "").strip() or \
            "Ollama/aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M"
        seq = [p, f]
    out, seen = [], set()
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# DeepSeek/云端 VLM 限流或报错时，ComfyUI 的 ImageCaptionNode **常常不在 status_str 标 error**，
# 而是把错误原文（如 "429 您的使用量已超出频率限制…"）写进 ShowText 当正常结果返回。
# 不主动识别这类"假成功"，就会把限流提示当反推结果发给主人（2026-10-14 踩坑：DeepSeek
# 免费档限流，429 文本被当反推串原样回给用户，看似"反推改死了"）。命中即抛 RevError 触发接力兜底。
_REV_ERR_MARKERS = (
    "429",
    "频率限制", "使用量已超出", "请求过于频繁", "访问频率", "被限流", "限流",
    "请稍后再试", "try again later", "please try again later",
    "too many requests", "rate limit", "ratelimit",
    "exceeded your", "quota", "request failed", "api error",
    "httperror", "connectionerror", "connection error",
    "timed out", "timeout", "traceback",
    " exception", "exception:", "error:", "error occurred",
)


def _rev_text_is_error(txt):
    if not txt:
        return False
    s = txt.lower()
    # 真成功是几十~几百词的反推串，不会命中这些错误标记；短且命中才判错
    return any(m in s for m in _REV_ERR_MARKERS)


def _rev_wf_once(cg, ccfg, g, cap, show, svc, timeout):
    """用指定 vlm_service 跑一次反推工作流，成功返回文本，失败抛 RevError。"""
    import random as _rnd
    g[cap]["inputs"]["vlm_service"] = svc
    # 换 seed 防命中上次缓存（同一张图换服务重试必须真跑）。
    # 已核实节点 fingerprint_inputs 含 vlm_service + seed ⇒ 换任一个都会重算。
    try:
        g[cap]["inputs"]["seed"] = _rnd.randint(1, 2 ** 31 - 1)
    except Exception:
        pass
    try:
        pid = cg._req(cg.base(ccfg) + "/prompt",
                      {"prompt": g, "client_id": "qqbot_rev"}, timeout=60,
                      )["prompt_id"]
    except Exception as e:
        raise RevError("反推工作流提交失败：%s" % str(e)[:150])
    t0 = time.time()
    while time.time() - t0 < max(30, int(timeout or 180)):
        try:
            h = cg._req(cg.base(ccfg) + "/history/" + pid, None, timeout=20) or {}
        except Exception:
            h = {}
        ent = h.get(pid)
        if ent:
            st = (ent.get("status") or {}).get("status_str")
            if st == "error":
                msg = ""
                for m in (ent.get("status") or {}).get("messages") or []:
                    if m and m[0] == "execution_error":
                        msg = (m[1] or {}).get("exception_message") or ""
                        break
                raise RevError("反推工作流执行出错：%s" % msg[:150])
            out = (ent.get("outputs") or {}).get(show) or {}
            arr = out.get("text") or []
            if arr and (arr[0] or "").strip():
                txt = _strip_think(arr[0].strip())
                # 句首媒介词清理（规则说不许写风格，模型照写；只动句首，安全）
                try:
                    txt, _hm = strip_medium_head(txt)
                    if _hm:
                        log.info("反推工作流：掐掉句首媒介词")
                except Exception as e:
                    log.warning("反推工作流：清理句首媒介词出错（不影响结果）%s", str(e)[:80])
                # 后处理硬过滤：掐掉模型编造的外观特征词（规则压不住的硬靠这里）
                try:
                    txt, dropped = strip_hallucinated_tags(txt)
                    if dropped:
                        log.info("反推工作流：掐掉疑似编造的 tag %s", dropped[:5])
                except Exception as e:
                    log.warning("反推工作流：过滤标签出错（不影响出图）%s", str(e)[:80])
                # 关键兜底：云端档限流时节点会把 429 原文当正常文本吐出来，
                # 必须识别"假成功"并抛错，才能触发 _rev_services 的下一档接力。
                if _rev_text_is_error(txt):
                    raise RevError("反推工作流 %s 返回错误文本（疑似限流/报错）：%s"
                                   % (svc, txt[:90]))
                return txt
            break
        time.sleep(2)
    raise RevError("反推工作流超时（%ss）" % int(timeout or 180))


def rev_wf_text(cfg, image_path, timeout=180, wf_name=None, sys_prompt=None):
    """跑「图片反推提示词」工作流拿反推串。返回 (文本, 后端简述)；失败抛 RevError。

    链：上传图→ wf_to_api(texts=None) → 补 LoadImage → POST /prompt → 轮询 /history
    取ShowText 的 `outputs[<id>]["text"][0]`。

    ⚠️ 2026-10-13 主人定：vlm_service **接力** —— 先 DeepSeek/deepseek-flash，
    跑挂/出错自动换 Ollama joycaption 补上（顺序见 _rev_services，可在 config 调）。
    """
    try:
        import comfy_gen as cg
    except Exception as e:      # 没装/导入失败也别拖垮反推
        raise RevError("反推工作流：comfy_gen 导入失败（%s）" % str(e)[:80])
    ccfg = cfg.get("comfyui") or cfg          # 有些调用方直接传 comfyui 段
    name = wf_name or REV_WF_NAME
    try:
        _wp, doc = cg.wf_load(ccfg, name)   # 返回 (路径, 文档)
    except Exception as e:
        raise RevError("反推工作流读不到（%s）：%s" % (name, str(e)[:120]))
    try:
        up = cg.upload_image(ccfg, image_path, subfolder="qqbot_rev")
    except Exception as e:
        raise RevError("反推工作流：传图失败 %s" % str(e)[:120])
    # wf_load 返回 (路径, 文档) —— 取 [1] 是doc（已核实 comfy_gen.wf_load 的签名）
    try:
        g, meta = cg.wf_to_api(doc, texts=None)
    except Exception as e:
        raise RevError("反推工作流转换失败：%s" % str(e)[:150])
    # 自己补 LoadImage（工作流里 image 槽没连线）
    cap = next((k for k, v in g.items()
                if v.get("class_type") == "ImageCaptionNode"), None)
    if cap is None:
        raise RevError("反推工作流里没有 ImageCaptionNode（工作流被主人改过？）")
    nid = str(max(int(k) for k in g) + 1)
    g[nid] = {"class_type": "LoadImage", "inputs": {"image": up}}
    g[cap]["inputs"]["image"] = [nid, 0]
    # 规则由 bot 侧接管（见上方说明）
    if sys_prompt is not None:
        g[cap]["inputs"]["custom_rule"] = True
        g[cap]["inputs"]["custom_rule_content"] = sys_prompt
    # 兜底：旧版节点残留会把 ollama_auto_unload 存成字符串（实测踩过），
    # 非 bool 一律归一成 True（= 本地模型用完释放显存，8G 卡需要）。
    if not isinstance(g[cap]["inputs"].get("ollama_auto_unload"), bool):
        g[cap]["inputs"]["ollama_auto_unload"] = True
    # 终端节点 = ShowText
    show = next((k for k, v in g.items()
                 if v.get("class_type", "").startswith("ShowText")), None)
    if show is None:
        raise RevError("反推工作流里没有 ShowText 节点（取不到文本）")
    # 服务接力：DeepSeek 优先 → Ollama 补位（工作流自带的值兜最后）
    svcs = _rev_services(cfg)
    orig = str(g[cap]["inputs"].get("vlm_service") or "").strip()
    if orig and orig not in svcs:
        svcs.append(orig)
    last = None
    for i, svc in enumerate(svcs):
        try:
            txt = _rev_wf_once(cg, ccfg, g, cap, show, svc, timeout)
            tag = svc.split("/", 1)[-1] if "/" in svc else svc
            if i > 0:
                log.info("反推工作流：%s 挂，换 %s 补位成功", svcs[0], svc)
            return txt, "wf/图片反推·%s" % tag[:28]
        except RevError as e:
            last = e
            log.warning("反推工作流：服务 %s 失败（%s）%s", svc, str(e)[:100],
                        "，换下一档" if i + 1 < len(svcs) else "（已是最后一档）")
    raise last or RevError("反推工作流：所有 VLM 服务都失败")



class RevError(Exception):
    pass


def _json_obj(text):
    """从模型回复里抠出第一个 JSON 对象（它爱加围栏和废话）。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _clean_latin(s, maxlen=48):
    """角色名/作品名只留拉丁字母与常见分隔，混了中文一律丢掉（danbooru 不认中文 tag）。"""
    import re as _re
    s = (s or "").strip().strip("《》「」“”\"'")
    if _has_cjk(s):
        return ""
    s = _re.sub(r"[^\x20-\x7E]", "", s)
    s = _re.sub(r"\s+", " ", s).strip(" ,.-")
    return s[:maxlen]


def _post_json(url, payload, headers=None, timeout=90):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        raise RevError("HTTP %s %s" % (e.code, detail))
    except Exception as e:
        raise RevError(str(e))


def _get(url, timeout=180):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RevError("HTTP %s" % e.code)
    except Exception as e:
        raise RevError(str(e))


def _data_url(image_path):
    p = Path(image_path)
    if not p.is_file():
        raise RevError("图不存在：%s" % p)
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return "data:%s;base64,%s" % (mime, b64)




# 2026-11-05 U11 转正：bot.py 5 处在调这两个函数，已是事实公共 API。
# 旧私有名保留为别名（一个版本周期），模块内部既有调用也不断链。
# （别名统一在文件末尾定义，这里只是说明。）
def fit_image_data_url(image_path, max_side=1280, max_bytes=0):
    """送视觉模型前把图压到「最长边 <= max_side」且「原始字节 <= max_bytes」。

    ⚠️ 2026-10-29 23:3x 重写（线上识图**全线失效**的根因就在这）：
      · 原版只给**本地 ollama** 用，云端档一律 `_data_url()` 发原图；
      · 而且 PNG 走的是**无损重编码** —— 8.3MB 的 2304px PNG 压到 1280 反而还是 PNG，
        体积没降下来（无损压不下照片），白压一场。
      实测 QQ 收到的图经常 6~8MB，于是：
        · nex-agi → `HTTP 400 image file size exceed limit: 524288`（512KB 硬上限）
        · 自定义站 kimi → `HTTP 413 request body too large`
        · 本地 ollama → 502/上下文爆
      三个档同时挂 ⇒ 画同款、看图接话全部「视觉模型全用不了」。
      · 现在：**一律转 JPEG**（照片用无损 PNG 是自找体积），并按字节预算逐档降质，
        1280px/q85 实测 358KB（base64 后约 478KB，稳过 512KB 那道门）。
    PIL 缺失/图损坏则回退原图（宁可发大图，也不能不出图）。"""
    try:
        from PIL import Image
        import io
        raw_size = Path(image_path).stat().st_size
        with Image.open(image_path) as src:
            w, h = src.size
            # 已经够小又够轻 ⇒ 原样发（省一次重编码，画质最好）
            if max(w, h) <= max_side and (not max_bytes or raw_size <= max_bytes):
                return _data_url(image_path)
            base = src.convert("RGB")
        # (边长, 质量) 逐档试；第一档就该过，后面的只兜异常大的图
        tries = [(max_side, 85), (max_side, 75), (int(max_side * 0.82), 75),
                 (int(max_side * 0.66), 70), (int(max_side * 0.50), 65)]
        fallback = None
        for side, q in tries:
            sc = min(1.0, side / float(max(w, h)))
            nw, nh = max(1, int(round(w * sc))), max(1, int(round(h * sc)))
            im = base if (nw, nh) == (w, h) else base.resize((nw, nh), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if fallback is None or len(data) < len(fallback[0]):
                fallback = (data, q, (nw, nh))
            if not max_bytes or len(data) <= max_bytes:
                return "data:image/jpeg;base64," + base64.b64encode(data).decode()
        # 全档都还超预算 ⇒ 用最小的那版（总比发 8MB 被 413 强）
        return "data:image/jpeg;base64," + base64.b64encode(fallback[0]).decode()
    except Exception:
        return _data_url(image_path)


# 云端免费视觉档的请求体硬上限（实测 nex-agi 512KB / 自定义站 413，base64 会放大 4/3
#  ⇒ 原始字节控制在 360KB 才稳）。本地 ollama 不受这条约束（它怕的是上下文 token）。
VL_CLOUD_IMG_BYTES = 360 * 1024
VL_CLOUD_IMG_SIDE = 1600


def _has_cjk(s):
    return any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" for c in s)


def _looks_degenerate(text):
    """输出是不是「崩了」：小模型偶尔会绕圈或刷同一个字符。
    判据（全都要能一眼看懂，别搞成玄学）：
      a) 太短（<8 字）—— 正常反推不可能这么短；
      b) 非字母数字/常规标点的字符占比 > 15%（@@@@、乱码、控制符）；
      c) 同一个非字母数字字符占了全文一半以上（@@@@@@@@…）之外的刷屏。
    命中就当这档没答上来（2026-09-16：本机 3b 在一张图上 929 字全是 "@"）。"""
    t = (text or "").strip()
    if len(t) < 8:
        return True
    ok = set(",. -'()[]{}:;!?/\\|\n\t\"·、。，")
    bad = sum(1 for c in t if not (c.isalnum() or c in ok))
    if bad / float(len(t)) > 0.15:
        return True
    try:
        from collections import Counter
        ch, n = Counter(t).most_common(1)[0]
        if n / float(len(t)) > 0.5 and not ch.isalnum() and ch not in ok:
            return True
    except Exception:
        pass
    return False


# 不该被当成角色名的占位/空值词（`_strip_cjk` 会把中文片段换成 "text"，
# 万一那条净化路径从别处漏进来，认人这里再挡一道 —— 2026-09-16）
NAME_PLACEHOLDERS = {"text", "none", "null", "nil", "unknown", "n/a", "na",
                     "未知", "不明", "无", "角色", "角色名"}


def _strip_cjk(s):
    """兑底净化：把混进提示词的中日文片段换成通用的 text 占位。"""
    import re as _re
    return _re.sub(r"[\u4e00-\u9fff\u3040-\u30ff]+", "text", s)


def _strip_think(s):
    """带思考的模型会把推理过程一起吐出来（<think>…</think>），正文只要最终结果。"""
    import re as _re
    s = _re.sub(r"<think\b.*?(?:</think>|$)", "", s or "", flags=_re.S | _re.I)
    return s.strip()


def _brief_err(e, maxlen=36):
    """把接口报错压成一两句话给群里看（原始 JSON 太长，别糊群友一脸）。"""
    import re as _re
    s = (str(e) or "").strip()
    head = s.split("{")[0].strip()[:24]
    m = _re.search(r'"message"\s*:\s*"([^"]*)"', s)
    if m and m.group(1).strip():
        return "%s·%s" % (head, m.group(1).strip()[:maxlen])
    return head or s[:maxlen]


def _candidates(pcfg, provider, model, cfg=None, chain=None, local_first=None,
                local_model=None):
    """排本次要试的 (后端, 模型) 顺序。

    本地 Ollama 视觉档**优先**（2026-09-16 主人定「本地优先，云端兜底」：免费、
    不限量、不审查），拿不到再按 chain 的免费云档接力；
    `reverse.vl_local_first=false` 可回到「云端优先、本地最后兜底」。

    ⚠️ 2026-10-07 加三个可选参数，让**聊天识图**能走自己那套（see.*）而不动反推：
      chain       = 自定义接力链（默认 VL_CHAIN）
      local_first = 本地优先与否（默认 reverse.vl_local_first）
      local_model = 本地档模型（默认 reverse.vl_local_model）
    """
    out, seen = [], set()

    def add(p, m):
        p = p or ""
        if not p or not (pcfg.get(p) or {}).get("url"):
            return
        key = (p, (m or "").lower())
        if key in seen:
            return
        seen.add(key)
        out.append((p, m))

    cfg = cfg or {}
    chain = tuple(chain or VL_CHAIN)
    lf = vl_local_first(cfg) if local_first is None else (local_first is not False)
    lm = local_model if local_model is not None else vl_local_model(cfg)
    # 本地档熔断期：不排它在前面，直接走云端（见 VL_LOCAL_BREAKER_*）。
    # 注意**不是**把它踢出链 —— 云端全挂时它仍是最后一档兜底，只是不再抢第一位。
    if lm and lf and vl_local_ok_to_try():
        add("ollama", lm)
    add(provider, model)
    for p, m in chain:
        if p == "ollama":
            # 链尾这档意思是「本机 Ollama 的 VL 模型，最后的兜底」。
            # 2026-09-18 修：原来只取 `ai.providers.ollama.model`，而那个键是**通用** ollama
            # 模型（可能配的是能聊天的模型、也可能是已被判定废弃的思考型 VL），
            # 于是「本地优先」用新档、链尾兜底却还是旧的废档（实测链尾仍是
            # `qwen3-vl-abliterated:4b`，一挂 43s）。改成**同一个 VL 专用档**：
            # `vl_local_model()` 已封装「reverse.vl_local_model > ai.providers.ollama.model」，
            # 这样换本地 VL 只需要改一处。
            om = lm or (pcfg.get("ollama") or {}).get("model") or ""
            if "vl" in om.lower():
                add(p, om)
        else:
            add(p, m)
    return out


def see_candidates(pcfg, cfg):
    """聊天识图专用接力链（see.*）。与反推的 `_candidates` 同一套排序逻辑，
    只是后端/模型/本地档/链全部可单独配置。

    ⚠️ 2026-10-07：「只本地」预设必须**真的只本地** —— 普通 `_candidates` 末尾会把
    `see_provider/see_model` 那一档也加进来，于是「只本地」还是会往云端发一次
    （实测敏感图照样被审核 400，等于没做到「完全不出网」）。这里对「只本地」特判。
    """
    preset = see_chain_preset(cfg)
    if preset.startswith("只本地") or preset.startswith("不联网"):
        lm = see_local_model(cfg) or (pcfg.get("ollama") or {}).get("model") or ""
        return [("ollama", lm)] if (lm and (pcfg.get("ollama") or {}).get("url")) else []
    return _candidates(pcfg, see_provider(cfg), see_model(cfg), cfg,
                       chain=see_chain(cfg),
                       local_first=see_local_first(cfg),
                       local_model=see_local_model(cfg))


# 送图前要关思考的档（模型名关键字）。实测这类模型 reasoning 吃几百 token，
# 预算不足 ⇒ 正文空 ⇒ 曾被误判成「不支持图片」。
_THINK_PRONE = ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro",
                "glm-4.1v-thinking", "thinking", "-think")


def _is_think_prone(pname, p, model):
    """这个云端档要不要在送图前关思考。"""
    blob = ("%s %s %s" % (pname, model or "", (p or {}).get("model") or "")).lower()
    return any(k in blob for k in _THINK_PRONE)


def vl_text(image_path, cfg, provider=None, model=None, sys_prompt=None,
             user_text="把这张图反推成绘画提示词。", purify_cjk=True, chain=None,
             chain_from_see=False, require_valid=None, allow_cjk=False, max_tokens=None):
    """按接力链逐个试视觉后端。返回 (文本, "后端/模型")；全挂了抛 RevError（信息已球友好化）。

    purify_cjk：输出混了中日文时要不要「就地净化成英文」。
      反推提示词要 True（图里带中文字幕时，宁可留个 text 占位也别整段丢掉）；
      **认人必须 False** —— 净化会把中文角色名（「黍」）变成占位符 "text"，
      而 "text" 是合法英文串，会绕过「中文名一律当没认出」那道护栏，
      拿一个叫 "text" 的假名字去查证（2026-09-16 实测踩到，`_verify_i2i` 的
      「identify 防认错（中文名）」就是被这个绕过的）。False 时宁可抛错当没认出。

    chain：显式指定接力链（(后端,模型) 列表）。给聊天识图用的 —— 传 `see_candidates()`
      的结果，让它走 see.* 那套排序，不再被反推的「本地优先」绑架（见 SEE_DEFAULTS 说明）。
    """
    pcfg = (cfg.get("ai") or {}).get("providers") or {}
    rcfg = cfg.get("reverse") or {}
    primary = provider or rcfg.get("provider") or VL_PROVIDER_DEFAULT
    pmodel = model or rcfg.get("vl_model") or VL_MODEL_DEFAULT
    if chain is None:
        chain = list(_candidates(pcfg, primary, pmodel, cfg))
        # 🔴 2026-10-30：`follow_brain` 必须**也管这条链**。
        #   原来只改了 see_provider/see_model（群聊看图那条），而 `describe()`（画同款反推）
        #   走的是 `_candidates()` + `VL_CHAIN`，链首写死 ⇒ 主人换了大脑模型，
        #   **群聊看图跟上了、画同款还在用老档**。这正是 10-29 栽过的同一个坑
        #   （当时只改 see.model，「群聊看图通了、画同款照样挂」）。
        #   做法：把大脑那档**提到链首**，原链整体保留当兜底（不砍任何档，挂了一样往下走）。
        if follow_brain_on(cfg):
            _bp, _bm = brain_vl_target(cfg)
            if _bp and _bm:
                chain = [(_bp, _bm)] + [x for x in chain if x != (_bp, _bm)]
    else:
        chain = list(chain)
    if not chain:
        raise RevError("config 里没有可用的视觉后端（ai.providers.%s 没配 url）" % primary)

    def once(pname, mdl, extra_user="", budget=None):
        p = pcfg[pname]
        is_local = (str(pname) == "ollama"
                    or str(p.get("type") or "").lower() == "ollama")
        # ⚠️ 思考型云端档（DeepSeek 系）送图前**关掉思考**（2026-10-07 实测）：
        #    deepseek-flash 是思考模型，reasoning 会吃掉几百 token（实测 284~712），
        #    预算不足时正文被截断成空串 —— 我早先就因此误判成「它不支持图片」。
        #    关掉后 reasoning=0、更快也更省。智谱那边用 extra.thinking 同款写法，
        #    不认识的档会忽略这个字段，无害。
        extra = OLLAMA_VL_PENALTY if is_local else None
        if not is_local and _is_think_prone(pname, p, mdl):
            extra = {"thinking": {"type": "disabled"}}
        return _call_vl(image_path, _vl_url(p), p.get("key") or "", mdl,
                        timeout=int(p.get("timeout") or 90),
                        extra_user=extra_user, sys_prompt=sys_prompt, user_text=user_text,
                        max_tokens=budget or vl_max_tokens(cfg, pname, p),
                        extra_body=extra,
                        local=is_local)

    def with_retry(pname, mdl, tries=2):
        text, last = "", None
        # `max_tokens`（2026-10-30 加）：调用方要极短回答时（读表情包情绪，只要几个字），
        # 别再按档位默认预算（几百 token）给 —— 模型会长篇大论，反而更像在描述画面。
        budget = max_tokens or vl_max_tokens(cfg, pname, pcfg[pname])
        is_local = (str(pname) == "ollama"
                    or str((pcfg.get(pname) or {}).get("type") or "").lower() == "ollama")
        i = 0
        while i < tries:
            i += 1
            try:
                text = once(pname, mdl, budget=budget).strip()
                break
            except Exception as e:
                last = e
                msg = str(e)
                # 思考型模型想太久被截断 —— 加预算重来（只在**云端**这么做）。
                # ⚠️ 本地那档（2026-09-16 实测 4b）**不能加预算**：它是 abliterated
                # （去审查）变体，真图反推时思考段会无限发散，给 2000 吐 4279 字、
                # 给 6000 吐 7008 字，永远是 finish=length + 正文空 ——
                # 再加预算只是**多等 30 秒还失败**，白耽误群友。本地档直接当没答上来，
                # 立刻交下一档（云端智谱 3~4s 就出结果，见 HANDOVER §6）。
                if ("思考段" in msg or "思考" in msg) and not is_local \
                        and max_tokens is None and budget < VL_LOCAL_MAX_TOKENS * 3:
                    budget = min(VL_LOCAL_MAX_TOKENS * 3, max(budget + 1, int(budget * 2)))
                    tries += 1
                    continue
                # 免费档常被限流（HTTP 429 / 智谱 1305）：歇一下再来一次，还不行就换档
                if ("429" in msg or "1305" in msg) and i < tries:
                    time.sleep(1.5)
                    continue
                raise
        if not text:
            raise last or RevError("模型回了空话")
        text = _strip_think(text)
        if not text:            # 整段都在 <think>…</think> 里（剥完就空）—— 当这档没答上来
            raise RevError("模型只回了思考段，没有正文")
        if _looks_degenerate(text):
            # 小模型的崩法：整段刷同一个字符（实测 2026-09-16 本机 3b 在第二张图上
            # 44 秒吐出 929 个 "@"），或乱码占比过高。这种当这档没答上来，换下一档，
            # 绝不能让它当反推结果灌进群里／提示词里。
            raise RevError("模型输出崩了（重复字符/乱码），当这档没答上来")
        if not _has_cjk(text):
            return text
        # ⚠️ allow_cjk（2026-10-07）：**描述类**调用（appearance）要的就是中文，
        #    不能走下面「逼它改英文 + 洗成空」那条路 —— 否则中文描述会被整段抹掉、
        #    还白等一次重试。认人（要不中文名字）与反推（要英文 tag）保持原行为。
        if allow_cjk:
            return text
        # 输出混了中文（常见于图里带中文字幕）：点名重试一次，还不行就就地净化
        try:
            retry = _strip_think(once(pname, mdl, extra_user="（重申：你刚才的输出里混了中文字符，"
                                                              "这次只输出纯英文，不要任何中文。）",
                                      budget=budget).strip())
            if retry and not _has_cjk(retry):
                return retry
        except Exception:
            pass
        if not purify_cjk:          # 认人：宁可当没认出，也不能拿 "text" 这种占位当名字
            raise RevError("模型输出里混了中文，这次认人不敢用")
        return _strip_cjk(text)

    fails = []
    steps = []                      # 本次接力明细（谁答的 / 谁挂了 + 原因），给网页排障用
    local_used = any(p == "ollama" for p, _m in chain)
    # ⚠️ 2026-10-07 关键修复：`require_valid` 时，「模型答了但答的是**没用的话**」不算成功，
    #    必须继续往下一档换 —— 实测本地 qwen2.5-vl-7b 认人时稳定返回
    #    `{"known": false, ...}`（合法 JSON、但等于「我没认出来」），旧逻辑把它当**成功**
    #    就 return 了 ⇒ 后面魔搭那档（唯一认对的）**根本没机会跑**，
    #    主人看到的「识图很差」有一半是这么来的（还白等 98 秒）。
    #    判据由调用方给：认人 = 解析出已知名字才算数；反推 = 有正文就算数（不传即旧行为）。
    def _valid(txt):
        if require_valid is None:
            return True
        try:
            return bool(require_valid(txt))
        except Exception:
            return True
    try:
        for pname, mdl in chain:
            is_local = (str(pname) == "ollama"
                        or str((pcfg.get(pname) or {}).get("type") or "").lower() == "ollama")
            _t = time.time()
            try:
                got = with_retry(pname, mdl)
                if not _valid(got):
                    # 答了但没用（如「没认出这个人」）→ 当这档没答上来，换下一档
                    if is_local:
                        _vl_local_note(False)
                    steps.append({"backend": pname, "model": mdl or "", "ok": False,
                                  "sec": round(time.time() - _t, 1),
                                  "err": "答了但没认出（换下一档）"})
                    fails.append("%s: 没认出" % (mdl or pname))
                    continue
                if is_local:
                    _vl_local_note(True)        # 本地档成功 → 清零熔断计数
                steps.append({"backend": pname, "model": mdl or "", "ok": True,
                              "sec": round(time.time() - _t, 1), "err": ""})
                _record_chain("%s/%s" % (pname, mdl or ""), steps, ok=True)
                return got, "%s/%s" % (pname, mdl or "")
            except Exception as e:
                if is_local:
                    _vl_local_note(False)       # 本地档失败 → 累计，够了就熔断
                fails.append("%s: %s" % (mdl or pname, _brief_err(e)))
                steps.append({"backend": pname, "model": mdl or "", "ok": False,
                              "sec": round(time.time() - _t, 1),
                              "err": vl_probe_reason(e)})
                continue
        _record_chain("全挂", steps, ok=False)
        raise RevError("视觉模型全用不了（%s）" % "；".join(fails[:3]))
    finally:
        # 本地那档跑完就把显存还回去（默认立刻卸；reverse.vl_local_keep_alive / see.keep_alive
        # 可让它留一会儿）。
        # ⚠️ 2026-10-07：显存驻留时长与模型名都要**按本次实际走的那条链**来 ——
        #    识图（see.*）和反推（reverse.*）的本地档与驻留设置是分开的，
        #    这里一律用 vl_local_model/vl_local_keep_alive 会把识图的设置写串。
        if local_used:
            om = (pcfg.get("ollama") or {}).get("model") or ""
            lm_used = next((m for p, m in chain if p == "ollama" and m), "") or om
            ka = see_keep_alive(cfg) if chain_from_see else int(rcfg.get("vl_local_keep_alive") or 0)
            _ollama_keep((pcfg.get("ollama") or {}).get("url") or "", lm_used, int(ka or 0))


# 最近一次识图/反推的接力明细：给网页「识图档」卡显示「谁答的、谁挂了、挂在哪一步」用。
# ⚠️ 只保留最近一条，且不落盘（重启即清）—— 这是**排障窗口**，不是历史记录。
#    2026-10-07 加：主人反馈「识图很差」时，最缺的就是「到底哪个模型在答、为什么别的没答上」。
_last_chain = [{}]


def last_chain():
    """最近一次视觉接力明细：{"via","ok","steps":[{"backend","model","ok","err","sec"}]}"""
    return dict(_last_chain[0] or {})


def _record_chain(via, steps, ok=True):
    _last_chain[0] = {"via": via, "ok": bool(ok), "steps": steps,
                      "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def vl_probe_reason(err):
    """把视觉模型的失败原因翻译成一句人话（网页/日志共用）。
    2026-10-07 加：调用方看到「识图失败」根本不知道是图太大、被审核、还是模型下架。"""
    s = str(err or "")
    low = s.lower()
    if "1301" in s or "contentfilter" in low:
        return "被内容审核拦截（云端免费档对敏感图会 400）"
    if "1305" in s or "429" in s or "访问量过大" in s:
        return "云端限流（免费档高峰期常见，稍后再试）"
    if "400" in s and ("context" in low or "length" in low or "token" in low):
        return "图太大超出上下文（本地档 n_ctx=4096 会撑爆）"
    if "思考段" in s:
        return "思考型模型把 token 全花在思考上（换非思考型模型）"
    if "崩了" in s or "重复字符" in s:
        return "模型输出崩了（重复字符/乱码）"
    if "no provider supported" in low or "model_not_found" in low or "不存在" in s:
        return "模型已下架/该后端不供给这个模型"
    if "timed out" in low or "timeout" in low:
        return "超时"
    return s[:80] or "未知原因"


def _call_vl(image_path, url, key, model, timeout=90, extra_user="", sys_prompt=None,
             user_text="把这张图反推成绘画提示词。", max_tokens=None, extra_body=None,
             local=False):
    """调一次 OpenAI 兼容的视觉接口，返回回复文本。失败抛 RevError。

    max_tokens：思考型模型（本地 Ollama 那只 Qwen3-VL 魔改）要留够思考段的地方，
      调用方用 vl_max_tokens() 算。给太小会拿到 `content=''`（思考没写完就被截断）。
    extra_body：额外塞进请求体的采样参数（本地档用它压复读，见 OLLAMA_VL_PENALTY）。
    local：True 时本地 Ollama 用 `_fit_image_data_url` 先压图，避免大图撑爆 4096 上下文。
      2026-10-29 23:3x 起**云端档也压**：免费云端视觉站有 512KB 请求体硬上限，
      QQ 收来的 8MB 大图直发就是 400/413（实测线上识图因此全线失效）。
      两边的预算不同 —— 本地怕 token，云端怕字节，所以参数分开传。
    """
    if local:
        img_url = _fit_image_data_url(image_path, max_side=1280)
    else:
        img_url = _fit_image_data_url(image_path, max_side=VL_CLOUD_IMG_SIDE,
                                      max_bytes=VL_CLOUD_IMG_BYTES)
    content = [{"type": "image_url", "image_url": {"url": img_url}},
               {"type": "text", "text": user_text + extra_user}]
    payload = {"model": model, "messages": [{"role": "system", "content": sys_prompt or DESC_SYS},
                                            {"role": "user", "content": content}],
               "max_tokens": int(max_tokens or VL_CLOUD_MAX_TOKENS), "temperature": 0.3}
    if extra_body:
        payload.update(extra_body)
    headers = {}
    if key:
        headers["Authorization"] = "Bearer " + key
    r = _post_json(url.rstrip("/") + "/chat/completions", payload, headers, timeout)
    try:
        msg = r["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
    except Exception:
        raise RevError("返回格式不对：%s" % str(r)[:120])
    if not text:
        # 思考型模型被 token 上限截断的典型样子：正文空的、思考段里一堆字。
        # 报清楚点，省得下次又当成「模型坏了」查半天（2026-09-16 本地档踩到）。
        think = str(msg.get("reasoning") or msg.get("thinking") or "")
        fin = str((r.get("choices") or [{}])[0].get("finish_reason") or "")
        if think and fin == "length":
            raise RevError("本地模型 token 全花在思考段了（把反推预算调大些）")
        raise RevError("模型回了空话")
    return text


REV_VL_SYS = (
    "你是绘画提示词工程师。看这张图，**只输出英文**：一串逗号分隔的英文提示词，"
    "按「主体 → 外观（发型/发色/瞳色）→ 服装单品 → 动作表情 → 构图镜头 → 场景 → 光线画风」"
    "的顺序写，40~80 个英文词。\n"
    "硬规则：禁止中文（一个汉字都不许出现）、禁止解释、禁止编号、禁止 JSON、"
    "禁止 markdown、禁止开场白。画面里若有文字水印，用 text 占位，不要照抄原文。"
)


def describe(image_path, cfg, provider=None, model=None):
    """vl 引擎。返回 (文本, "实际用的后端/模型")。主档失败会自动退到下一个免费档。

    ⚠️ 2026-10-29 修（主人报「/厚涂图生图 不生效」的最后一环）：这里原来**不传
    `sys_prompt`**，用的是 `_vl_text` 默认的 user_text「把这张图反推成绘画提示词。」——
    对通用对话模型（魔搭 nex-agi 这类）约束太弱，它老老实实**用中文描述画面**；
    而反推链路是 `allow_cjk=False` + `purify_cjk=True`：见中文先逼它改英文重试一次，
    还是中文就把整段洗成空串 ⇒ 报「模型回了空话」。
    实测（2026-10-29）：nex-agi 看图问答 1.5s 正常，反推 11.5s 稳定「空话」——
    而它是当时**唯一活着**的视觉档（ollama 502 / deepseek 402 / 智谱 401 / 硅基 402），
    于是画同款必然反推失败、图生图压根没开始。
    现在给它一份明确的英文输出契约，第一轮就该给英文 tag，不再靠事后净化兜。
    """
    return _vl_text(image_path, cfg, provider, model, sys_prompt=REV_VL_SYS)



# ============ 两段式识图（2026-10-07 主人定：加联网检索）============
# 为什么不再让视觉模型直接报角色名（实测依据，同一张丰川祥子图）：
#   · 本地 qwen3-vl-4b → 报 Kogami / 山田凉 / **Atatier**（把水印昵称当人名），信心 0.85~0.95
#   · deepseek-flash / glm-4v-flash → 老实说认不出
#   · 魔搭 Nex-N2.5-Pro → 唯一答对（Sakiko Togawa / MyGO!!!!! 0.96），**但 3 次里挂 2 次**
# ⇒ 免费视觉模型**没有稳定的动漫角色识别能力**：要么认不出，要么自信瞎编。
#   但它们的「**看画面**」能力完全够用（发色/瞳色/服饰/标志物描述得又准又细）。
# 所以拆成两段各干各擅长的：
#   ① appearance()：视觉模型**只描述**，绝不让它猜名字（描述错了也只是描述，不会认错人）
#   ② 调用方拿这段描述去**联网检索**「这个特征的角色是谁」→ 再复核 → 才敢往群里说
APPEARANCE_SYS = (
    "你看图，**只用中文客观描述画面里这个人的外观特征**，不要猜是谁、不要说作品名。"
    "按这个顺序写，用分号隔开，一句话说完："
    "性别与年龄感；发型（长短/直卷/发色/有无马尾或呆毛）；瞳色；"
    "服装每件单品（颜色+款式，如「白色水手服」「黑色百褶裙」）；"
    "显眼配饰或标志物（发饰、蝴蝶结、徽章、武器、耳机等）；"
    "**如果画面上有角色名或作品名的文字水印，原样抄下来**。"
    "不要描述背景、构图、光线。不要输出 JSON、不要解释。40~80 字。"
)


def appearance(image_path, cfg, provider=None, model=None):
    """只让视觉模型**描述外观**（不认人、不猜名字）。

    返回 (描述文本, "实际用的后端/模型")；全挂抛 RevError。
    ⚠️ 不传 require_valid：这里不要求它认出谁，能描述出画面就算成功。
    ⚠️ allow_cjk=True：描述本来就是中文，不能被「逼改英文/洗成空」那条路抹掉。
    """
    return _vl_text(image_path, cfg, provider, model,
                    sys_prompt=APPEARANCE_SYS,
                    user_text="描述这个人的外观特征。",
                    purify_cjk=True, allow_cjk=True)


# ---- 看图问答（2026-10-08 新增：@着它 + 带图（含引用的图）问一句话，照着图回答）----
# 与「认角色」是**两件不同的事**，别混：
#   · 认角色 = 要**知识**（这角色叫什么、出自哪部）⇒ 模型记不住就瞎编，必须联网，慢。
#   · 看图问答 = 只考**看画面**（数鸡蛋、什么天气、穿的什么颜色）⇒ 视觉模型的原始能力
#     就够用，**不需要联网、也没有「认错人」的风险** ⇒ 走便宜的 API 链，几秒出结果。
QA_SYS = (
    "你看图回答用户的问题。**只照着图里看得见的东西答**，"
    "看不清、或图里没有的就直说不知道，**绝对不要编**。"
    "只用中文、口语、简短（问数量就只给数量，最多补半句）。不要解释、不要分点、不要复述问题。"
)


def answer_image(image_path, question, cfg, provider=None, model=None):
    """照着图回答问题（**不认人**）。返回 (答案文本, "实际用的后端/模型")；全挂抛 RevError。

    ⚠️ allow_cjk=True：答案就该是中文，不能被「逼改英文 + 洗成空」那条路抹掉。
    ⚠️ 不传 require_valid：这是「看画面」任务，模型答了就算数 ——
    答案对不对用户自己一眼能判，不像认人那样「认错人的代价很大」。
    """
    return _vl_text(image_path, cfg, provider, model,
                    sys_prompt=QA_SYS,
                    user_text=(question or "这张图里有什么？").strip(),
                    purify_cjk=True, allow_cjk=True)


# ---- 通用看图（2026-10-10 主人要求：识图不能只认人，发什么图都得「看懂」）----------
# ⚠️ 背景（主人原话）：「bot 的聊天端的识图功能，不要只是识别人物啊，发的图啥都可能是，
#    主要是要看懂」。
#    原先群里纯发图 → bot.py:sim_pic_chat **只跑 identify（认角色）**，而 ID_SYS 明确写了
#    「原创角色、路人、真实人物、动物、风景、照片一律 known=false」⇒ 群友发风景照、猫、
#    外卖截图、梗图、手办、报错截图时 bot **一句话都不说**。这就是「只会认人」的根因。
#    这条补的就是那块空缺：**不管图里是什么，先客观说清「这是啥、里面有啥」**。
#
# 与另外两条的分工（别混）：
#   · identify       = 认**是谁**（需要作品知识，模型会瞎编，必须联网复核，慢）
#   · answer_image   = 照图**答一个问题**（用户问了才用）
#   · describe_scene = **看懂这张图**（不问也看；非人物也看得懂）← 本函数
#
# ⚠️ 提示词里**不许举具体作品/角色当例子**（老教训：示范值会被照抄成答案）。
# ⚠️ 明确要求「图里有文字就抄下来」—— 群友发的截图/聊天记录/报错只有靠这条才看得懂。
# ---------------- 表情包：**读情绪 + 认不认得出角色/梗**（10-30 第六修 → 10-31 第八修）--------
# 🔴 第六修（主人「你直接跳过表情包啊，你可真会找办法」）：
#    第五修为了止住「逐张点评」，走了最省事的一条路 —— 群里刷表情包**整条跳过**。
#    主人看穿了：那不是懂了，是躲了。群里刷表情包是在**表达情绪**，
#    正常人读得懂（笑/无语/委屈），只是不会去描述「这图里画了什么」。
# 🔴 第八修（主人「如果能认出角色是谁，或者梗的含义，也能说一句，
#    整体说话的频率不要太频繁」）：
#    光有情绪还不像人 —— 群里甩个**大家都认识的角色/梗**，正常人一眼认出来，
#    会顺着接一句「蛋黄哥这表情…」「狗头保命是吧」。所以窄任务扩成两段：
#      · 前半 **情绪**（必答，2~6 字）
#      · 后半 **角色/梗的名字**（**只在认得出时**写，认不出就留空）
#    ⚠️ 两条铁律没变：**绝不描述画面**；**认不出就留空，绝不猜**。
#    ⚠️ 认出来 ≠ 每次都要说：说不说由 bot 那边的密度闸/冷却说了算（见 `_sim_density_ok`）。
# 调用方（bot.py）只在「这一整段值得判一次」的时候才花这一次调用（有预算闸），
# 读到的东西只作为**上下文的一段语气**喂给大脑，不单独发出去。
VIBE_SYS = (
    "你看的是一张聊天里的**表情包/贴图**。它不表达画面内容，它表达的是**情绪或态度**。\n"
    "只输出**一行**，格式是：\n"
    "情绪|角色或梗\n"
    "· 前半（必填）：它传达的情绪，2~6 个汉字，如 笑死 / 无语 / 委屈 / 震惊 / 摆烂 / 破防。\n"
    "· 后半（选填）：如果你**确定**这张表情是某个大家都认识的动漫/游戏角色、"
    "或者某个网络流行梗、表情包 IP，就写它的**名字**（如 蛋黄哥、蘑菇头、狗头、"
    "滑稽、熊猫头、悲伤蛙、可达鸭、猫和老鼠、真香、打工人）；"
    "**认不出就留空，竖线也不要写**，绝不许猜。\n"
    "· 只认得情绪 ⇒ 只写前半。真看不出情绪 ⇒ 只输出「看不出情绪」。\n"
    "**严禁**：描述画面、提任何物体/动物/人物外貌/图上文字、解释为什么、"
    "说「这张图」「图片里」、加除竖线外的任何符号。"
)


def read_vibe(image_path, cfg, provider=None, model=None):
    """读一张表情包的**情绪 + 认不认得出角色/梗**。返回 (情绪, 角色或梗, via)。

    认不出角色/梗时第二项返回空串 —— **绝不猜**（猜错在群里比不说更尴尬）。
    全挂返回 ("", "", via)。

    ⚠️ 走 `see.*` 那条链（跟群聊看图同一条），不另开配置 —— 主人换识图模型时一块生效。
    ⚠️ 输出很短（几个字），所以 token 预算极低、很快；但仍**必须有预算闸**：
        群友连刷十张表情包，不能十次都去调模型（调用方负责，见 bot.py 的 `_vibe_budget`）。
    """
    pcfg = (cfg.get("ai") or {}).get("providers") or {}
    raw, via = _vl_text(image_path, cfg, provider or see_provider(cfg),
                        model or see_model(cfg),
                        sys_prompt=VIBE_SYS,
                        user_text="这张表情包是什么情绪？如果认得出是哪个角色或梗，用「情绪|角色或梗」回答。",
                        purify_cjk=True, allow_cjk=True, max_tokens=48,
                        chain=see_candidates(pcfg, cfg),
                        chain_from_see=True)
    vibe, known = _vibe_split(raw)
    return vibe, known, via


# 原始输出按「情绪|角色或梗」拆两段（模型爱用全角竖线/斜杠，都认）
_VIBE_SEP = re.compile(r"\s*[|｜/／]\s*")


def _vibe_split(raw):
    """把原始输出拆成 (情绪, 角色或梗)。两段各自独立清洗，**一段废掉不影响另一段**。"""
    s = _strip_think(raw or "")
    s = s.strip().splitlines()[0] if s.strip() else ""
    parts = _VIBE_SEP.split(s, 1)
    vibe = _vibe_clean(parts[0] if parts else "")
    known = _known_clean(parts[1]) if len(parts) > 1 else ""
    return vibe, known


# 情绪词清洗：去掉模型爱加的引号/句号/前缀，以及**描述画面的措辞**。
_VIBE_JUNK = re.compile(
    r"(这张图|图片里|表情包|画面|表达|情绪|态度|意思是|看来|大概|似乎|我觉得|"
    r"这张|一个|一只|[A-Za-z]+)|[「」『』\"'“”‘’()（）\[\]【】。，,\.!！?？:：;；\s]")

# 「这不是情绪词，这是在描述画面」的硬判据（2026-10-30 第六修）。
# 🔴 为什么必须有：模型很容易答「一个笑脸」「画了个人在哭」—— 那就是**描述画面**，
#    正是主人骂的「逐张评头论足」。这种回答必须在**这里**就拦掉，
#    绝不能让它洗一洗变成「人在哭」混进上下文（P 段实测就是这么漏的）。
_VIBE_DESC_RE = re.compile(
    r"(画|描|图|有人|人物|个人|角色|脸|眼睛|嘴|手|头发|衣服|背景|颜色|表情|动作|"
    r"上面|里面|屏幕|文字|写着|拿着|举着|躺|站|坐|跑|跳|哭|笑(?!死))")


def _vibe_clean(text):
    """把 `read_vibe` 的原始输出洗成 2~6 个字的情绪词；洗不出来 / 像描述画面 ⇒ 返回空。

    返回空 = 调用方用「纯语气」占位（绝不编一个情绪出来）。
    """
    s = _strip_think(text or "")
    s = s.strip().splitlines()[0] if s.strip() else ""
    s = _VIBE_JUNK.sub("", s)
    s = s.strip(" ·-—~")
    if len(s) > 8:            # 说多了 = 它在描述画面，不像话 ⇒ 当没读出来
        return ""
    if not s:
        return ""
    # 「看不出情绪」这类占位回答 = 没读出来（别把它当情绪写进上下文）
    if re.match(r"^(看不出|不确定|不知道|无法判断|说不清|没有情绪)", s):
        return ""
    if _VIBE_DESC_RE.search(s):
        # 含「画/人/脸/哭/动作…」这类词 ⇒ 它在描述画面，不是在说情绪 ⇒ 拒收
        return ""
    return s


# ---------------- 「角色/梗名」的清洗（2026-10-31 第八修）----------------
# 🔴 这一段比情绪更危险：情绪说错最多是无聊，**角色/梗认错在群里是当众出丑**
#    （把猫和老鼠认成别的可就社死了）。所以判据更狠：
#      · 只有**名字**能过（2~10 字，不含动词/句子成分）
#      · 凡是「看不出/不确定/无/没有」这种占位 ⇒ 当没认出来
#      · 凡是在**描述**（含 画/图/一个/穿着/写着/表情包 …）⇒ 当没认出来
#    认不出 = 返回空，上层就只当语气，**绝不编**。
_KNOWN_JUNK = re.compile(
    r"(表情包|贴图|这张图|图片|画面|这是|那是|它是|这个|那个|一个|一只|"
    r"应该是|好像|可能是|我猜|我觉得|角色是|名字是|角色名|出处|来自|应该|"
    r"[A-Za-z]+)|[「」『』\"'“”‘’()（）\[\]【】。，,\.!！?？:：;；\s|｜/／]")

# 「这不是在报名字，这是在描述画面 / 在打太极」的硬判据
_KNOWN_BAD = re.compile(
    r"(看不出|不确定|不知道|无法|说不清|不认识|不清楚|认不出|没有|无|"
    r"画|图|描述|穿着|戴着|拿着|举着|写着|上面|里面|背景|颜色|动作|表情|"
    r"人物|有人|站着|坐着|躺着|跑|跳|"
    # ⚠️ 下面这几个是**描述性泛称**，不是名字：「黄色的卡通角色」这种必须拒收，
    #    否则它就会变成「这个表情是一个黄色的卡通角色」混进上下文 = 又是评头论足。
    r"角色|卡通|动漫|形象|东西|动物|的)")


def _known_clean(text):
    """把「角色/梗」那半段洗成一个**名字**（2~10 字）；像描述/像占位 ⇒ 返回空。"""
    s = _strip_think(text or "")
    s = s.strip().splitlines()[0] if s.strip() else ""
    s = _KNOWN_JUNK.sub("", s)
    s = s.strip(" ·-—~")
    if not s or len(s) > 10 or len(s) < 2:
        return ""
    if _KNOWN_BAD.search(s):
        return ""
    return s


SCENE_SYS = (
    "你看图，**只用中文客观说明这张图是什么、画面里有什么**，"
    "目的是让一个看不见这张图的人听完就能想象出画面。"
    "40~90 字，一两句话说完，不要分点、不要 markdown、不要开场白。\n"
    "按情况挑着说，不用面面俱到：\n"
    "1) 先说这图**属于哪一类**：动漫或游戏插画、真人照片、动物、风景、食物、物品或商品、"
    "截图（聊天记录／代码／网页／报错／账单）、表情包或梗图、海报封面、手写或文档、"
    "地图或图表 —— 拿不准就说「不确定是什么类型」。\n"
    "2) 再说**主体**：是什么、大概有几个、什么颜色形状材质、在做什么或处于什么状态。\n"
    "3) 画面里有人就补：性别年龄感、发型发色、穿什么、动作表情；"
    "**绝不猜他是谁，不许报角色名、作品名、画师名**。\n"
    "4) **图里有文字且看得清，就把关键文字原样抄下来**（标题、聊天内容、报错信息、"
    "弹幕、招牌、菜单）；抄不清就直说「有字但看不清」。\n"
    "5) 场景类补一句：地点、环境、天气、白天还是晚上。\n"
    "硬性规矩：只写画面里**看得见**的东西；看不清或图里没有的就直说不知道，"
    "**绝对不要编**；不评价好不好看；不要写「这张图展示了」这类废话；只用中文。\n"
    # ---- 末尾那行「要不要去认人」的标记（调用方据此决定要不要花 30~40s 走联网认人链）----
    # ⚠️ 为什么不用调用方自己扫关键词判：实测**扫不准** —— 模型写「简笔食物插画」
    #    （一盘鸡蛋）和「游戏概念插画」（一条龙）都带「插画」，界面截图里还出现了
    #    「动画人物」这种 UI 文字 ⇒ 关键词闸门等同虚设，每张图都白等半分钟。
    #    让**看着图的模型**自己答一个是非题，一次调用顺带拿到，最省也最准。
    "最后**另起一行**只输出这一行标记（不要带别的）：\n"
    "ROLE: yes —— 画面主体是**动漫/游戏作品里的角色**（二次元人形角色）\n"
    "ROLE: no  —— 其余一切（风景、动物、食物、物品、真人照片、截图、"
    "原创画、非人形角色、看不懂的图）\n"
    "拿不准就写 no。"
)


# 通用看图输出末尾那行 `ROLE: yes/no` 标记（决定要不要走慢的联网认人链）
_ROLE_LINE = re.compile(r"^\s*ROLE\s*[:：]\s*(yes|no)\b.*$", re.I | re.M)


def _split_role(text):
    """把通用看图输出里的 `ROLE: yes/no` 标记行切出来。

    返回 (去掉标记行的正文, True/False/None)；**None = 模型没按格式给**，
    调用方要保守处理（见 bot.py:sim_pic_chat —— 拿不准就去认一次，宁可慢也别漏）。
    """
    s = (text or "").strip()
    if not s:
        return "", None
    flag, hit = None, False
    for m in _ROLE_LINE.finditer(s):
        hit = True
        flag = (m.group(1).strip().lower() == "yes")
    if not hit:
        return s, None
    body = re.sub(r"\n{3,}", "\n\n", _ROLE_LINE.sub("", s)).strip()
    return body, flag


def describe_scene(image_path, cfg, provider=None, model=None):
    """通用看图：无论图里是风景/物品/动物/截图/梗图/真人照，客观说清画面。

    返回 **(中文描述, "后端/模型", 是否像作品角色图)**；全挂抛 RevError。
    第三个值 True/False，拿不到就是 None（模型没按格式给标记行）。

    ⚠️ allow_cjk=True：描述本来就要中文，不能被「逼它改英文 + 洗成空」那条路抹掉。
    ⚠️ 不传 require_valid：这是「看画面」任务，说得出话就算数（没有「认错人」那种代价）。
    ⚠️ 走 **see.*** 那套识图接力链（不是反推链）—— 与认人/看图问答同一条，
       改配置一处生效。
    """
    pcfg = (cfg.get("ai") or {}).get("providers") or {}
    raw, via = _vl_text(image_path, cfg, provider or see_provider(cfg),
                        model or see_model(cfg),
                        sys_prompt=SCENE_SYS,
                        user_text="看清楚这张图，用中文说明它是什么、画面里有什么。",
                        purify_cjk=True, allow_cjk=True,
                        chain=see_candidates(pcfg, cfg),
                        chain_from_see=True)
    desc, flag = _split_role(raw)
    return desc, via, flag


def identify(image_path, cfg, lookup=None, provider=None, model=None, require_lookup=False):
    """认图里是谁：VL 先认人，认出名字再交给 lookup 去联网查设定与 danbooru tag。

    lookup(name, series) -> (资料段, tag串)，由调用方注入（bot 侧接 SubjectBook + 联网搜索）；
    不传 lookup 就只用模型自己的记性，不给 tag。
    require_lookup=True（**聊天识图用**）：lookup 查不到这个角色就判为「没认出」。
      实测模型自述 confidence 不可信（同一张图能报出 4 个全错的名字），
      只有能在资料库里查到的名字才敢往群里说。画同款（要出图）不要开，
      免得起因查证服务抖动把出图顶掉。
    返回 {"known", "name", "series", "confidence", "brief", "tags", "via"}；认不出返回 known=False，
    **任何失败都不抛异常**（反推/生图主流程不该被认人拖垮）。
    """
    out = {"known": False, "name": "", "series": "", "confidence": 0.0,
           "brief": "", "tags": "", "via": ""}
    pcfg = (cfg.get("ai") or {}).get("providers") or {}
    rcfg = cfg.get("reverse") or {}
    if not pcfg:
        return out
    # ⚠️ 2026-10-07：认人/看图的**后端与模型改走 see.***（见 SEE_DEFAULTS 的实测说明）。
    #    之前它跟着 reverse.provider / reverse.vl_model 走，于是「本地优先」把实测最差的
    #    本地 7b 排到了第一位（认不出），唯一答对的魔搭被压到链尾第 4 位。
    #    调用方显式传 provider/model 时仍以参数为准（画同款那边会传）。
    pname = provider or see_provider(cfg)
    model = model or see_model(cfg)

    # ⚠️ 2026-10-07 关键修：把「解析出名字」这步**下推进接力链**当有效性判据。
    #    旧写法先跑 `_vl_text` 拿一整串文本、再在外面解析一次 —— 问题是 `_vl_text`
    #    认为「模型答了话」就算成功（本地 7b 稳定回 `{"known":false}` 也算答了），
    #    于是它**当场 return**，后面唯一认对的魔搭根本没机会跑（实测白等 98 秒还认不出）。
    #    现在传 require_valid：只有真解析出「已知名+置信度」才算这档成功，否则换下一档。
    def _accept(txt):
        d0 = _json_obj(_strip_think(txt)) or {}
        nm0 = _clean_latin(d0.get("name"))
        if nm0.lower() in NAME_PLACEHOLDERS:
            nm0 = ""
        try:
            c0 = float(d0.get("confidence") or 0)
        except Exception:
            c0 = 0.0
        # ⚠️ 0.6 → 0.75（2026-10-07 实测）：本地 4b 与 DeepSeek 都会拿 0.9~0.95 的信心
        #    报**错**名字（同一张祥子图报出山田凉/Atatier/Mutsumi Wakaba 三种）。
        #    0.6 这道闸等于没闸；抬到 0.75 后实测那些乱认会被挡在门外，
        #    真正认得准的档（魔搭识别作品名 0.95）照样进得来。
        return bool(d0.get("known") and nm0 and c0 >= 0.75)

    try:
        # 走同一条免费接力链：主档挂了自动换档，认人不会因为某个模型下架就整体失效
        # purify_cjk=False：中文名不许被净化成占位符（见 _vl_text 的说明）
        raw, used = _vl_text(image_path, cfg, pname, model,
                             sys_prompt=ID_SYS, user_text="这张图里画的是谁？",
                             purify_cjk=False,
                             chain=(None if provider else see_candidates(pcfg, cfg)),
                             chain_from_see=(not provider),
                             require_valid=_accept)
        raw = _strip_think(raw)
    except Exception as e:
        out["via"] = "%s·失败(%s)" % (pname, _brief_err(e))
        return out
    d = _json_obj(raw) or {}
    nm, se = _clean_latin(d.get("name")), _clean_latin(d.get("series"))
    if nm.lower() in NAME_PLACEHOLDERS:      # 占位符不算名字
        nm = ""
    if se.lower() in NAME_PLACEHOLDERS:
        se = ""
    try:
        conf = float(d.get("confidence") or 0)
    except Exception:
        conf = 0.0
    # 拿不准一律当认不出：认错人会把画面带成另一个人，比不认糟得多
    # ⚠️ 门槛 0.6 → 0.75（2026-10-07）：与 _accept 同步。实测模型报**错**名字时信心也常在
    #    0.75~0.95（本地 4b 报 "Atatier" 都敢给 0.85），单靠数字挡不住 ⇒
    #    这只是一道粗筛，真正防乱认靠调用方复核（sim_pic_chat 查到才说，见 bot.py）。
    if not (d.get("known") and nm and conf >= 0.75):
        out["via"] = "%s·没认出" % used
        return out
    out.update(known=True, name=nm, series=se, confidence=round(min(conf, 1.0), 2),
               via=used)
    if lookup:
        try:
            brief, tags = lookup(nm, se)
            out["brief"] = (brief or "").strip()[:600]
            out["tags"] = _clean_tags(tags)
        except Exception:
            pass
        # ⚠️ 2026-10-07：**认图（聊天识图）必须查得到才作数**。
        #    模型自述的信心已被证明不可信（实测同一张图报出 4 个全错的名字，
        #    信心还都是 0.75~0.95），所以这里把「资料库查不到」当作**认不出**。
        #    只对认图生效：画同款那条路（provider 显式传入）查不到也照样画，
        #    不能因为查证服务抖动就把主人的出图任务顶掉。
        if require_lookup and not out["brief"]:
            out["via"] = "%s·查不到这个人（判为没认出）" % used
            out.update(known=False, name="", series="", confidence=0.0,
                       brief="", tags="")
            return out
    return out


def _clean_tags(tags, maxlen=240):
    """查证回来的 tag 串过一遍闸：混中文/`<占位符>` 一律砍掉（danbooru 不认，还会误导模型）。"""
    import re as _re
    s = (tags or "").strip()
    if not s:
        return ""
    keep = []
    for t in _re.split(r"[,\n]", s):
        t = _re.sub(r"\s+", " ", t).strip().strip(".")
        if not t or _has_cjk(t) or ("<" in t or ">" in t):
            continue
        if len(t) > 40:
            continue
        keep.append(t.lower())
    return ", ".join(keep[:14])[:maxlen]


def compose(base, ident=None, extra=""):
    """拼最终提示词：认出来的角色 tag 在最前（模型认角色最吃这个），反推描述居中，群友补充最后。"""
    parts = []
    if ident and ident.get("tags"):
        parts.append(ident["tags"])
    if base:
        parts.append(base.strip().strip(","))
    if extra:
        parts.append(extra.strip().strip(","))
    return ", ".join(p for p in parts if p)[:1500]


def who_line(ident):
    """给群消息用的一句话：「认出是 XX（XX 作品），已按它的设定画」；没认出返回空串。"""
    if not ident or not ident.get("known"):
        return ""
    who = (ident.get("name") or "").strip()
    if not who:      # 豆包识图有时只认出画师、说不出是谁——那就别在群里喊「认出是「」」
        return ""
    se = ident.get("series") or ""
    return "认出是「%s」%s，按它的设定画的" % (who, "（%s）" % se if se else "")


def caption_vl(image_path, cfg):
    """JoyCaption Beta One 本地反推（2026-10-01 加，社区公认最强打标/描述档，天生无审查）。
    一次调用同时出：第一行 danbooru tag 串 + 「CN:」开头的中文画面实况一句。
    模型没配/失败返回 None（调用方退回原 VL 链）。

    🔴 2026-10-24 主人问「反推必须开 comfyui 吗，我的那些规则配 ollama 可以吗」——
    这条路**本来就不经过 ComfyUI**（直接打 Ollama 的 /api/chat）。但原来用的是写死的
    一套「出 tag + 一行中文」，**不认** `REV_WF_SYS` 那套规则。现在加了开关：
      `reverse.caption_sys` = "joy"（默认，原行为）| "rules"（用 REV_WF_SYS 那套规则，
      输出连贯英文自然语言，不出 tag）。这样不装/不开 ComfyUI 也能按主人的规则反推。
    """
    pcfg = (cfg.get("ai") or {}).get("providers") or {}
    rcfg = cfg.get("reverse") or {}
    mdl = (rcfg.get("caption_model") or "").strip()
    p = pcfg.get("ollama") or {}
    if not mdl or not p.get("url"):
        return None
    _mode = str(rcfg.get("caption_sys") or "joy").strip().lower()
    _use_rules = _mode in ("rules", "rev", "wf", "自定义", "主人规则")
    _use_short = _mode in ("short", "短", "精简")
    if _use_short:
        # 压缩版规则：给"吃不住长 prompt"的模型用（实测对 JoyCaption 也只降低一点违规率，
        # 真正管用的还是后面的 strip_style_lead，但短版对 qwen 系更稳、更快）
        sys_p = ("You are an objective image annotation tool. Describe what is actually visible: "
                 "subject and count, appearance, clothing item by item, pose and expression, "
                 "shot and camera, composition, lighting, environment, textures. "
                 "Write one flowing English paragraph. Do NOT name the medium, genre or art "
                 "style (no painting, illustration, anime, digital art, photorealistic, "
                 "cel shading). Do NOT invent details you cannot see. No commentary.")
        user = "Describe this image."
        _max_tok = int(rcfg.get("caption_max_tokens") or 900)
    elif _use_rules:
        # 用主人那套规则（客观、不许编、不许说画风、连贯自然语言）——与 wf 工作流同一份
        sys_p = REV_WF_SYS
        user = ("Describe this image strictly following the rules above. "
                "Output ONE flowing paragraph of plain English natural language. "
                "No tag list, no Chinese, no explanation, no markdown.")
        _max_tok = int(rcfg.get("caption_max_tokens") or 900)
    else:
        sys_p = ("You are a diligent image captioning assistant. You always write very accurate, "
                 "objective captions for the image. You never editorialize or add interpretation. "
                 "You never make up information not present in the image.")
        user = ("Tag this image with a danbooru-style tag list: lowercase, comma separated. "
                "Output ONLY the tags on the first line, no explanations. "
                "Then output a second line starting with 'CN:' — one short Chinese sentence "
                "summarizing the image (real photo or anime/illustration, subject, hair and eye "
                "color, outfit, pose).")
        _max_tok = 512
    # 2026-10-03：改走原生 /api/chat —— /v1 端点会吞掉 options.num_gpu，
    # 全量进显存和 ComfyUI 挤 8G 必 OOM 崩服务（实测原生 num_gpu=20 只占 60% 显存）。
    # keep_alive=0 用完即卸，不长期占显存。
    import base64 as _b64
    url = str(p.get("url") or "").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    try:
        data_url = _fit_image_data_url(image_path)      # 压到长边 1280，防爆上下文
        img_b64 = data_url.split(",", 1)[-1]
        payload = {
            "model": mdl,
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user, "images": [img_b64]},
            ],
            "stream": False,
            "keep_alive": 0,
            "options": {
                "num_predict": _max_tok,
                "temperature": 0.3,
                "num_gpu": int(rcfg.get("caption_num_gpu") or 20),
                "frequency_penalty": 0.8,
                "presence_penalty": 0.4,
            },
        }
        r = _post_json(url + "/api/chat", payload, {}, timeout=int(p.get("timeout") or 120))
        text = ((r.get("message") or {}).get("content") or "")
    except Exception:
        return None
    text = _strip_think(text or "").strip()
    if not text:
        return None
    tags, cn = "", ""
    if _use_rules or _use_short:
        # 规则/短规则档输出是连贯段落，可能换行 —— 整段拿走，别只留第一行
        _lines = [x.strip() for x in text.splitlines() if x.strip()]
        for _l in _lines:
            if _l[:3].upper() == "CN:":
                cn = _l[3:].strip()
        tags = " ".join(x for x in _lines if x[:3].upper() != "CN:").strip()
        # 摘掉「A digital painting of X's Y style.」这类纯画风首句（默认开）
        if rcfg.get("caption_strip_style", True):
            tags = strip_style_lead(tags)
    else:
        for line in text.splitlines():
            l = line.strip()
            if l[:3].upper() == "CN:":
                cn = l[3:].strip()
            elif l and not tags:
                tags = l
    if not tags or len(tags) < 6 or _has_cjk(tags):
        return None
    return tags, cn[:120]


def tags_wd14(image_path, cfg):
    """wd14 引擎：图拷进 ComfyUI input/，调 WD14-Tagger 的 HTTP API，打完即删。返回 tag 串。"""
    ccfg = cfg.get("comfyui") or {}
    base = (ccfg.get("url") or COMFY_BASE_DEFAULT).rstrip("/")
    comfy_dir = Path(ccfg.get("comfy_dir") or "")
    input_dir = comfy_dir / "input"
    if not comfy_dir or not input_dir.is_dir():
        raise RevError("没找到 ComfyUI 的 input 目录（config comfyui.comfy_dir）")
    src = Path(image_path)
    if not src.is_file():
        raise RevError("图不存在：%s" % src)
    tmp = input_dir / ("_qqbot_rev_%s%s" % (uuid.uuid4().hex[:8], src.suffix.lower() or ".png"))
    shutil.copyfile(src, tmp)
    try:
        u = "%s/pysssss/wd14tagger/tag?%s" % (base, urllib.parse.urlencode(
            {"filename": tmp.name, "type": "input"}))
        raw = _get(u)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise RevError("WD14 返回不是 JSON：%s" % raw[:120])
    if isinstance(data, dict):
        # 兼容 dict 形态：取最长的字符串值，或按置信度过滤
        if "tags" in data and isinstance(data["tags"], str):
            s = data["tags"]
        elif data:
            pairs = [(k, v) for k, v in data.items() if isinstance(v, (int, float))]
            if pairs:
                s = ", ".join(k for k, v in sorted(pairs, key=lambda x: -x[1])
                              if v >= WD14_THRESHOLD_NOTE)
            else:
                s = ", ".join(str(v) for v in data.values() if isinstance(v, str))
        else:
            s = ""
    else:
        s = str(data)
    s = s.strip().strip(",")
    if not s:
        raise RevError("WD14 没打出 tag（图可能太抽象）")
    return s




POLISH_SYS = (
    "你是 danbooru 标签整理器。输入是 WD14 打标器从一张图反推出的英文标签串。"
    "只做减法和排序，【严禁新增任何原图没有的内容】："
    "1) 删掉对出图无意义的过度细碎词（如孤立的 nose 这类单部位词）；"
    "2) 删掉互相冲突的标签（保留与画面主体更匹配的那个）；"
    "3) 去重、合并同义项；"
    "4) 按「角色/主体→外貌特征→服装→动作姿势→构图镜头→光照氛围→画风质量」顺序重排；"
    "5) 第一行输出「TAGS:」+整理后的英文标签串（逗号分隔）；"
    "6) 第二行输出「CN:」+一句中文概括画面。除此之外不要输出任何其他文字。"
)


def _chat_text(cfg, messages, timeout=12, max_tokens=None):
    """拿 config 里的文本 LLM（openai 兼容口）跑一次小任务；任何失败返回 None。
    反推洗串专用：不 import bot（循环依赖），这里精简一份。"""
    ai = cfg.get("ai") or {}
    rcfg = cfg.get("reverse") or {}
    name = rcfg.get("polish_provider") or ai.get("provider") or ""
    p = (ai.get("providers") or {}).get(name) or {}
    if not p or p.get("type", "openai") == "ollama" or not (p.get("url") or p.get("base_url")):
        return None
    url = (p.get("url") or p.get("base_url") or "").rstrip("/")
    payload = {"model": p.get("model", ""),
               "messages": messages,
               "temperature": 0.3,
               "max_tokens": int(max_tokens or p.get("max_tokens") or 500)}
    try:
        d = _post_json(url + "/chat/completions", payload,
                       {"Authorization": "Bearer " + (p.get("key") or "")}, timeout=timeout)
        msg = (d.get("choices") or [{}])[0].get("message", {})
        return _strip_think(msg.get("content") or "")
    except Exception:
        return None


_TAGDICT = None


def _tagdict(cfg):
    """懒加载 WeiLin 词库 → {归一化tag: 中文}。快照复制到临时目录避开 WAL 竞争；失败给空表。"""
    global _TAGDICT
    if _TAGDICT is not None:
        return _TAGDICT
    d = {}
    try:
        import os, shutil, sqlite3, tempfile
        base = os.path.join(cfg.get("comfyui", {}).get("comfy_dir", "") or "",
                            "custom_nodes", "WeiLin-Comfyui-Tools", "user_data")
        tmp = tempfile.mkdtemp(prefix="wl_")
        for fn, sql in (
            ("userdatas_zh_CN_danbooru.db", "SELECT tag, translate FROM danbooru_tag"),
            ("userdatas_zh_CN_tags.db", "SELECT text, desc FROM tag_tags"),
        ):
            fp = os.path.join(base, fn)
            if not os.path.isfile(fp):
                continue
            dst = os.path.join(tmp, fn)
            shutil.copy2(fp, dst)
            con = sqlite3.connect(dst)
            try:
                for k, v in con.execute(sql):
                    if k and v:
                        d.setdefault(str(k).replace("_", " ").strip().lower(),
                                     str(v).strip())
            finally:
                con.close()
    except Exception:
        pass
    _TAGDICT = d
    return d


def _annotate(tags, cfg, limit=8):
    """洗串后的 tag 串 → ['1girl=一个女孩', ...]（最多 limit 条，查不到的跳过）。"""
    out = []
    td = _tagdict(cfg)
    if not td:
        return out
    for t in [x.strip() for x in tags.split(",") if x.strip()]:
        if _has_cjk(t):
            continue
        cn = td.get(t.replace("_", " ").strip().lower())
        if cn:
            out.append("%s=%s" % (t.replace("_", " "), cn.split("，")[0].split("、")[0][:24]))
        if len(out) >= limit:
            break
    return out


def _polish_tags(raw, cfg):
    """wd14 原始串 → (整理后tag串, 中文概括)。关闭/失败一律 None（照旧发原始串）。
    2026-10-01 加：wd14 生串带碎词和歧义项（真人相片打出 hair tubes，
    拿去出图会被底模先验带成初音味），先洗一遍再发。"""
    rcfg = cfg.get("reverse") or {}
    if rcfg.get("polish") is False or not (raw or "").strip():
        return None
    out = _chat_text(cfg, [
        {"role": "system", "content": POLISH_SYS},
        {"role": "user", "content": raw[:1500]},
    ], timeout=30, max_tokens=4000)  # 该模型思考段就吃 400+ tokens，2000 会被吃光导致正文空
    if not out or "TAGS" not in out:
        return None
    tags = cn = ""
    for line in out.splitlines():
        l = line.strip()
        if l[:5].upper() == "TAGS:":
            tags = l[5:].strip(" ,")
        elif l[:3].upper() == "CN:":
            cn = l[3:].strip()
    if not tags or len(tags) < 10 or _has_cjk(tags):
        return None
    return tags, (cn or "")[:80], _annotate(tags, cfg)


def run(image_path, cfg, engine=None):
    """统一入口。返回 (ok, 主体, 实际引擎简述, 说明)。

    2026-10-05 起主体与说明**物理分离**：主体只有提示词本身（纯英文、可直接复制去用），
    说明（引擎/耗时/中文画面描述/操作提示）走第 4 个值，由调用方**另发一条消息**。
    早先是拼在同一句里 —— 主人复制提示词会把说明一起粘走；而且那份带说明的文本还会被
    画同款当提示词喂进 ComfyUI（中文注释白烧 token、可能污染画面）。
    老调用方只解前 3 个值仍可运行。
    """
    rcfg = cfg.get("reverse") or {}
    engine = (engine or rcfg.get("engine") or "vl").lower()
    t0 = time.time()
    # 2026-10-04 主人定：图片反推走 ComfyUI 那条工作流（图片反推提示词.json）。
    # 规则文本由 bot 侧的 REV_WF_SYS 接管（去掉了移除限制那几条，理由见其docstring）。
    if engine.startswith("wf"):
        t0 = time.time()
        try:
            txt, via = rev_wf_text(cfg, image_path,
                                   timeout=int(rcfg.get("wf_timeout") or 300),
                                   wf_name=(rcfg.get("wf_name") or None),
                                   sys_prompt=REV_WF_SYS)
        except RevError as e:
            # 工作流档跑挂**不静默**：明说失败原因，让主人知道是工作流问题不是图的问题
            return False, "反推工作流跑挂了：%s\n（工作流：%s，可在网页「反推工作流」换一份）" % (
                str(e)[:200], rcfg.get("wf_name") or REV_WF_NAME), "wf·失败", ""
        dt = time.time() - t0
        # 2026-10-05：主体只留提示词；引擎/耗时说明走第 4 个返回值，由调用方另发一条
        # —— 早先拼在同一句里，主人复制提示词会把这段说明一起粘走。
        return True, txt, "wf·%s" % via, "（ComfyUI 工作流反推 %.0f 秒·%s）" % (dt, via)
    if engine.startswith("cap"):
        got = caption_vl(image_path, cfg)
        if got:
            tags, cn = got
            # 2026-10-24：模型可换（caption_model），标签别再写死 "JoyCaption" ——
            # 换成本地 qwen2.5-vl 时那样写会骗人，主人以为还是 JoyCaption 在跑。
            _mdl = (rcfg.get("caption_model") or "").split("/")[-1].split(":")[0]
            note = "（本地 Ollama 反推·%s，不走 ComfyUI；要 wd14 串发「反推 tags」）" % (
                _mdl or "本地模型")
            if cn:
                # 「画面实况」是中文描述、不是提示词 —— 也归说明，主体保持纯英文可粘贴
                note = "画面实况：" + cn + chr(10) + note
            return (True, tags, "cap·本地%s" % (_mdl or "Ollama"), note)
        engine = "vl"       # 模型没配/挂了 → 退回原 VL 链
    if engine.startswith("wd14"):
        try:
            s = tags_wd14(image_path, cfg)
        except Exception as e:
            return False, str(e), "wd14", ""
        if engine != "wd14raw":
            polished = _polish_tags(s, cfg)
            if polished:
                tags, cn, notes = polished
                mid = []
                if cn:
                    mid.append("画面：" + cn)
                if notes:
                    mid.append("关键词：" + "，".join(notes))
                note = "（已洗串：剔除碎词/冲突项，要原始串发「反推 raw」）"
                if mid:
                    # mid 是中文画面/关键词说明、不是提示词 —— 归说明
                    note = "\n".join(mid) + chr(10) + note
                return (True, tags, "wd14·打标+洗串", note)
        return True, s, "wd14·本机ComfyUI打标", ""
    try:
        s, used = describe(image_path, cfg)
    except Exception as e:
        return False, str(e), "vl", ""
    return True, s, "vl·%s" % used, ""


# 2026-11-05 U11 转正（同上）：
_vl_text = vl_text

_fit_image_data_url = fit_image_data_url
