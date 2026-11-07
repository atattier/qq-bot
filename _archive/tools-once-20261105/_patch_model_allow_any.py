# -*- coding: utf-8 -*-
"""修「custom 大脑换模型被拒」（2026-10-29 晚，主人截图：『「模型选择」只能填：
buddy/deepseek-v4.1-flash』）。

病根：拉取弹窗（/api/custom_models，653 个）选完写回「模型选择」旋钮（ai.model），
而 enum 校验的候选来自 _brain_model_choices() —— custom 不在 MODEL_MENU 里，
候选 = [当前值] ⇒ 弹窗选谁都被「只能填：当前值」打回。前端弹窗与后端校验脱节。

修法：enum 校验加 `allow_any`（放行任意非空值），「模型选择」旋钮启用。
为什么安全：模型名本来就不可枚举（网关 653 个还常变），填错了网关自己报错，
跟手改 config 等价；候选下拉/拉取弹窗照旧渲染，不受影响。
（教训红线：绝不能把 653 个塞回下拉 —— 10-29 早主人已经为此爆过一次。）
"""
import io
import os

B = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "bot.py")
s = io.open(B, encoding="utf-8").read()


def rep(old, new, tag):
    global s
    c = s.count(old)
    assert c == 1, "%s 锚点命中 %d" % (tag, c)
    s = s.replace(old, new)


# ---- 1) enum 校验放行 allow_any ----
OLD1 = '''        for v in vals:
            if v.lower() == s.lower():
                return v, ""
        return None, "只能填：%s" % "、".join(vals)'''
NEW1 = '''        for v in vals:
            if v.lower() == s.lower():
                return v, ""
        if entry.get("allow_any") and s.strip():
            # 自定义入口的模型名不可枚举（网关几百个还常变）：放行任意非空值，
            # 填错了网关自己会报 —— 跟手改 config 等价。候选/拉取弹窗照旧渲染。
            return s.strip(), ""
        return None, "只能填：%s" % "、".join(vals)'''
rep(OLD1, NEW1, "enum校验")

# ---- 2) 「模型选择」旋钮启用 allow_any ----
OLD2 = '''     "get": _brain_model_get, "after": _brain_model_set, "alias": ["模型档", "选模型"],
     "allow_empty": ["默认", "（默认）", "default"],'''
NEW2 = '''     "get": _brain_model_get, "after": _brain_model_set, "alias": ["模型档", "选模型"],
     "allow_empty": ["默认", "（默认）", "default"],
     # allow_any（2026-10-29 晚）：custom 大脑的模型来自拉取弹窗（653 个不可枚举），
     # 候选列表只有当前值 ⇒ 弹窗选完写回必被「只能填」打回。放行任意非空值。
     "allow_any": True,'''
rep(OLD2, NEW2, "旋钮属性")

io.open(B, "w", encoding="utf-8", newline="").write(s)
print("allow_any 补丁已写入")
