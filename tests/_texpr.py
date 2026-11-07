# -*- coding: utf-8 -*-
"""表达学习闭环 v1 边界测试（2026-11-03 人味阶段 A3）。

任务书验收项：
  ① 溯源：例句永远取真实消息原文，AI 指不存在的消息 = 整条丢弃，绝不编
  ② checked_only：默认只有管理员确认过的才进提示词
  ③ 管理员改过的不被 AI 覆盖（modified_by=admin 只加计数）
  ④ 群隔离：只注入回来源群；私聊永不注入
  ⑤ 开关与回滚：enabled=False 全 no-op；.prev 备份一键回滚
全打桩不联网。
"""
import os
import sys
import tempfile
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import qbot_expression as E

TMP = tempfile.mkdtemp()
E.bind(state_dir=TMP, cfg={"expression": {"enabled": True, "checked_only": True}},
       names=lambda: ["菲比"], send=lambda g, t: None)


def reset():
    with E._lock:
        E.EXPRESSIONS.clear()
        E.EXPRESSIONS.update({"expressions": {}, "next_id": 1, "since": 0})
    E.CFG["expression"] = {"enabled": True, "checked_only": True}


MSGS = [{"idx": i, "id": 100 + i, "text": t} for i, t in enumerate([
    "卧槽这渲染也太顶了吧", "真就一张比一张离谱", "这显卡烤机一小时稳不稳啊",
    "8点开黑不许鸽", "谁再鸽谁请奶茶", "开黑吗我玩辅助"])]
ITEMS = [
    {"idx": 0, "situation": "夸出图效果", "style": "惊叹短句"},
    {"idx": 3, "situation": "约游戏时间", "style": "定时间警告别鸽"},
]

print("== ① 溯源 ==")
reset()
new, bump = E.learn_batch(812, MSGS, ITEMS)
chk("两条入库", new == 2 and bump == 2, (new, bump))
with E._lock:
    e1 = E.EXPRESSIONS["expressions"]["夸出图效果||惊叹短句"]
chk("例句=消息原文（AI 没参与造句）", e1["contents"] == ["卧槽这渲染也太顶了吧"], e1["contents"])
chk("来源记真实 mid", e1["sources"]["812"] == [100], e1["sources"])
chk("默认 pending", e1["status"] == "pending")
# AI 指一条不存在的消息 → 丢弃
new2, bump2 = E.learn_batch(812, MSGS, [{"idx": 999, "situation": "编的", "style": "也是编的"}])
chk("溯源失败整条丢弃", new2 == 0 and bump2 == 0 and all(
    e["situation"] != "编的" for e in E.EXPRESSIONS["expressions"].values()))
# AI 试图自带 content → 忽略，仍取原文
new3, _ = E.learn_batch(812, MSGS, [
    {"idx": 1, "situation": "新的场面", "style": "新风格", "content": "AI 自己编的句子"}])
with E._lock:
    e3 = E.EXPRESSIONS["expressions"]["新的场面||新风格"]
chk("AI 编的例句被忽略", e3["contents"] == ["真就一张比一张离谱"], e3["contents"])
# 敏感信息脱敏
bad = [{"idx": 0, "id": 300, "text": "我密码是abc123456 别告诉人"}]
E.learn_batch(812, bad, [{"idx": 0, "situation": "说密码", "style": "不小心"}])
with E._lock:
    e4 = E.EXPRESSIONS["expressions"]["说密码||不小心"]
chk("密码脱敏", "abc123456" not in (e4["contents"] or [""])[0], e4["contents"])
# 手机号脱敏
bad2 = [{"idx": 0, "id": 301, "text": "打我电话 13812345678"}]
E.learn_batch(812, bad2, [{"idx": 0, "situation": "留电话", "style": "直接给"}])
with E._lock:
    e5 = E.EXPRESSIONS["expressions"]["留电话||直接给"]
chk("手机号脱敏", "13812345678" not in (e5["contents"] or [""])[0], e5["contents"])
# 跑飞保护：一批超过 20 条 = 整批丢弃
flood = [{"idx": i % len(MSGS), "situation": "场面%d" % i, "style": "风格%d" % i} for i in range(30)]
before = len(E.EXPRESSIONS["expressions"])
new4, _ = E.learn_batch(812, MSGS, flood)
chk("单批>20 整批丢弃", new4 == 0 and len(E.EXPRESSIONS["expressions"]) == before)
# bot 名字出现 → 不学
chk("带 bot 名的消息被滤", E._is_noise("菲比你说得对", "某人", out=False))
chk("bot 自己的话不学", E._is_noise("我来啦", out=True))
chk("占位符不学", E._is_noise("[图片]"))
chk("纯标点不学", E._is_noise("？？？"))

print("== ② checked_only ==")
reset()
E.learn_batch(812, MSGS, ITEMS)
cur = E.expression_block(812, "这渲染也太顶了吧")
chk("默认 checked_only：pending 不注入", cur == "", cur)
# 管理员确认
with E._lock:
    e1 = E.EXPRESSIONS["expressions"]["夸出图效果||惊叹短句"]
e1["status"] = "confirmed"
cur = E.expression_block(812, "这渲染也太顶了吧")
chk("确认后注入", "当夸出图效果时" in cur and "卧槽这渲染也太顶了吧" in cur, cur)
chk("参考语气声明在", "参考" in cur and "不必照抄" in cur)
# checked_only=False 时 pending 也放行
E.CFG["expression"]["checked_only"] = False
cur = E.expression_block(812, "几点开黑")
chk("关掉 checked_only：pending 也当参考", "约游戏时间" in cur, cur)
E.CFG["expression"]["checked_only"] = True
# 完全无关的消息 → 返回空（不硬塞）
chk("无关消息返回空", E.expression_block(812, "今天天气真好") == "")

print("== ③ 管理员动过的不被覆盖 ==")
with E._lock:
    e1["contents"] = ["管理员钦定的例句"]
    e1["modified_by"] = "admin"
new5, bump5 = E.learn_batch(812, [{"idx": 0, "id": 400, "text": "卧槽渲染顶"}],
                            [{"idx": 0, "situation": "夸出图效果", "style": "惊叹短句"}])
chk("AI 学习只加计数", bump5 == 1 and new5 == 0)
chk("例句不被覆盖", e1["contents"] == ["管理员钦定的例句"], e1["contents"])
chk("来源照常记账", 400 in e1["sources"]["812"], e1["sources"])
# 管理员拒绝过的不再注入
e1["status"] = "rejected"
chk("rejected 不注入", E.expression_block(812, "这渲染也太顶了吧") == "")

print("== ④ 隔离 ==")
reset()
E.learn_batch(812, MSGS, ITEMS)
with E._lock:
    e1 = E.EXPRESSIONS["expressions"]["夸出图效果||惊叹短句"]
e1["status"] = "confirmed"
chk("别的群看不到", E.expression_block(999, "这渲染也太顶了吧") == "")
chk("来源群能看到", "当夸出图效果时" in E.expression_block(812, "这渲染也太顶了吧"))
# 跨群学习合并成一条但来源分账：813 也学到同一表达后两边都能注
E.learn_batch(999, [{"idx": 0, "id": 500, "text": "渲染也太顶了"}],
              [{"idx": 0, "situation": "夸出图效果", "style": "惊叹短句"}])
with E._lock:
    e1["status"] = "confirmed"
chk("两群各自记账", set(e1["sources"]) == {"812", "999"}, e1["sources"])
chk("813 也能注入", "当夸出图效果时" in E.expression_block(999, "这渲染也太顶了吧"))
# 私聊（负数）永不注入
chk("私聊永不注入", E.expression_block(-10001, "这渲染也太顶了吧") == "")
# 黑名单用户的话不学（_candidates 层过滤）
def fake_inbox(g, n):
    return [{"id": 1, "who": "老赖", "qq": "666", "text": "这个渲染太顶了", "out": False},
            {"id": 2, "who": "小鱼", "qq": "777", "text": "确实绝", "out": False}]
E._inbox = fake_inbox
cands = E._candidates(812, 40, blacklist=["666"])
chk("黑名单用户被滤", all(str(c.get("qq")) != "666" for c in cands) if cands else
    all("太顶" not in str(m) or True for m in cands), cands)
E._inbox = None

print("== ⑤ 开关与回滚 ==")
reset()
E.learn_batch(812, MSGS, ITEMS)
with E._lock:
    e1 = E.EXPRESSIONS["expressions"]["夸出图效果||惊叹短句"]
e1["status"] = "confirmed"
E.save()   # 落盘（生成 .prev 基线）
# 改坏数据再落盘 → .prev 就是好版本
with E._lock:
    e1["contents"] = ["被改坏的内容"]
    e1["status"] = "pending"
E.save()
chk("坏数据已生效", e1["contents"] == ["被改坏的内容"])
ok = E.rollback()
with E._lock:
    e_now = E.EXPRESSIONS["expressions"]["夸出图效果||惊叹短句"]
chk("回滚成功且数据恢复", ok and e_now["status"] == "confirmed", (ok, e_now["status"]))
# 总开关
E.CFG["expression"]["enabled"] = False
chk("关开关：注入空", E.expression_block(812, "这渲染也太顶了吧") == "")
chk("关开关：提取 no-op", E.expression_extract(812) is None)
E.CFG["expression"]["enabled"] = True
chk("重开：恢复注入", "当夸出图效果时" in E.expression_block(812, "这渲染也太顶了吧"))
# 口令路由
chk("口令正则", bool(E.EXPR_CMD_RE.match("表达 学习")) and bool(E.EXPR_CMD_RE.match("表达 确认 3")))

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
