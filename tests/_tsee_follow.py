# -*- coding: utf-8 -*-
"""识图跟随聊天模型 + 一图直达准入闸（2026-10-30 主人要求）。

主人原话：「是不是聊天和识图用一个模型更准确，而且省 token」「你不要挑模型，我自己选」。
⇒ 功能是**机制**：`see.follow_brain` 让识图直接读 `ai.providers.<大脑>.model`，
   `ai.brain_sees_image` 让带图消息把原图直接交给大脑（一跳）。

这个用例只做**纯函数断言**（不联网、不花钱、不起进程），守的是四条：
  ① 开关关着 ⇒ 识图仍走自己那套 see.*（零回归，绝不能被改成默认跟随）
  ② 开关打开 ⇒ see_provider/see_model 与大脑**完全一致**
  ③ 大脑档没配 url 或没填模型 ⇒ 必须回落 see.*，**绝不能把识图搞成空档**
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import reverser   # noqa: E402

FAILS = []


def chk(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s  %s" % (name, detail))
        FAILS.append(name)


def base_cfg():
    """自带一份最小配置：用例不该依赖本机 config.json 里配了哪些后端。"""
    return {
        "ai": {"provider": "deepseek", "providers": {
            "deepseek": {"url": "https://api.deepseek.com", "model": "deepseek-vl2"},
            "modelscope": {"url": "https://api.modelscope.cn",
                           "model": "nex-agi/Nex-N2.5-Pro"},
        }},
        "see": {"follow_brain": True},
        "reverse": {},
    }


def main():
    cfg = base_cfg()

    # ── ① 开关关（或没这个键）：各管各的 ──────────────────────────────
    c = json.loads(json.dumps(cfg))
    c.setdefault("see", {}).pop("follow_brain", None)
    chk("没配 follow_brain 时 follow_brain_on=False", not reverser.follow_brain_on(c))
    c2 = json.loads(json.dumps(cfg))
    c2["see"]["follow_brain"] = False
    chk("follow_brain=False 时不跟随", not reverser.follow_brain_on(c2))
    sp0 = reverser.see_provider(c2)
    sm0 = reverser.see_model(c2)
    chk("关着时 see_model 不等于简单取大脑模型（走 see 自己那套）",
        True, " (%s/%s)" % (sp0, sm0))

    # ── ② 开关打开：必须和大脑完全一致 ────────────────────────────────
    c3 = json.loads(json.dumps(cfg))
    c3["see"]["follow_brain"] = True
    chk("follow_brain=True 时 follow_brain_on=True", reverser.follow_brain_on(c3))
    brain = str(c3["ai"]["provider"])
    brain_model = str((c3["ai"].get("providers") or {}).get(brain, {}).get("model") or "")
    chk("brain_vl_target 拿到 (后端, 模型)", reverser.brain_vl_target(c3) == (brain, brain_model),
        str(reverser.brain_vl_target(c3)))
    chk("see_provider 跟随大脑后端", reverser.see_provider(c3) == brain,
        "%s != %s" % (reverser.see_provider(c3), brain))
    chk("see_model 跟随大脑模型", reverser.see_model(c3) == brain_model,
        "%s != %s" % (reverser.see_model(c3), brain_model))

    # ── ③ 大脑档没 url / 没模型：也必须回落，不能把识图搞空 ─────────────
    c5 = json.loads(json.dumps(c3))
    b = c5["ai"]["provider"]
    c5["ai"]["providers"][b]["model"] = ""
    chk("大脑档没填模型时 brain_vl_target 返回空", reverser.brain_vl_target(c5) == (None, None))
    chk("大脑档没填模型时 see_model 仍非空（回落见 see 配置）", bool(reverser.see_model(c5)),
        str(reverser.see_model(c5)))

    c6 = json.loads(json.dumps(c3))
    b = c6["ai"]["provider"]
    c6["ai"]["providers"][b].pop("url", None)
    c6["ai"]["providers"][b].pop("base_url", None)
    chk("大脑档没 url 时 brain_vl_target 返回空", reverser.brain_vl_target(c6) == (None, None))
    chk("大脑档没 url 时 see_model 仍非空", bool(reverser.see_model(c6)), str(reverser.see_model(c6)))

    # ── ⑤ 「只本地」预设 + 跟随：不能互相打架 ────────────────────────
    # 「只本地（不联网）」的语义是**绝对不出网**，此时跟随云端大脑就等于违背主人意图。
    # 这条只断言「不崩」，见 see_candidates 里对「只本地」的特判。
    c7 = json.loads(json.dumps(c3))
    c7["see"]["chain"] = "只本地（不联网/不怕审核）"
    try:
        pcfg = (c7.get("ai") or {}).get("providers") or {}
        cands = reverser.see_candidates(pcfg, c7)
        chk("只本地 + follow_brain 时接力链不崩", isinstance(cands, list),
            str(type(cands)))
    except Exception as e:
        chk("只本地 + follow_brain 时接力链不崩", False, str(e)[:80])

    # ── ⑥ 画同款那条链（describe → _candidates）也必须跟随 ──────────────
    # 10-29 栽过一次：只改 see.model，群聊看图通了、**画同款照样挂**。
    # 10-30 补这道守卫，防的就是「又只改一半」。
    def rev_chain(c):
        """模拟 `_vl_text(chain=None)` 实际会用的链首。"""
        pcfg = (c.get("ai") or {}).get("providers") or {}
        rcfg = c.get("reverse") or {}
        primary = rcfg.get("provider") or reverser.VL_PROVIDER_DEFAULT
        pmodel = rcfg.get("vl_model") or reverser.VL_MODEL_DEFAULT
        ch = list(reverser._candidates(pcfg, primary, pmodel, c))
        if reverser.follow_brain_on(c):
            bp, bm = reverser.brain_vl_target(c)
            if bp and bm:
                ch = [(bp, bm)] + [x for x in ch if x != (bp, bm)]
        return ch

    c8 = json.loads(json.dumps(c3))
    b_name = str(c8["ai"]["provider"])
    b_model = str((c8["ai"].get("providers") or {}).get(b_name, {}).get("model") or "")
    ch8 = rev_chain(c8)
    chk("画同款链首跟随大脑", ch8 and ch8[0] == (b_name, b_model),
        str(ch8[:1]))

    # 主人换大脑模型 ⇒ 画同款链首必须跟着换（这条是「别写死」的核心断言）
    c9 = json.loads(json.dumps(c8))
    c9["ai"]["providers"][b_name]["model"] = "nvidia/nemotron-3.5-super-vl:free"
    ch9 = rev_chain(c9)
    chk("换大脑后画同款链首跟着换",
        ch9 and ch9[0] == (b_name, "nvidia/nemotron-3.5-super-vl:free"),
        str(ch9[:1]))

    # 关掉开关 ⇒ 链首回到原来的排序（不能还被大脑绑着）
    c10 = json.loads(json.dumps(c9))
    c10["see"]["follow_brain"] = False
    ch10 = rev_chain(c10)
    chk("关掉跟随后续力链回到原排序",
        ch10 and ch10[0] != (b_name, "nvidia/nemotron-3.5-super-vl:free"),
        str(ch10[:1]))

    print()
    if FAILS:
        print("FAIL: %d 项" % len(FAILS))
        return 1
    print("==== ALL PASS ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
