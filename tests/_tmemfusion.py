# -*- coding: utf-8 -*-
"""阶段 D 实验项：记忆检索 RRF 融合 + 动态阈值（SAFE，全离线）。

默认 `memory.fusion="linear"`，旧行为逐字节不变；本测试锁死：
  ① 默认仍是线性打分；
  ② RRF 模式分数归一化 0~1（recall_min 继续有效）、多信号都靠前的排最前；
  ③ 动态阈值（P75）会收缩候选，但至少保留 min_results 条；
  ④ 切回 linear 排序与分数恢复。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


from qbot import qbot_memory as M  # noqa: E402

M.embed = lambda q: None          # 不联网：走字符二元组语义
_CFG = {"memory": {"hybrid": True, "enabled": True}}
TMP = tempfile.mkdtemp(prefix="tmemrrf_")
M.bind(state_dir=TMP, cfg=_CFG)

FACTS = [
    ("10001", "阿伟", "阿伟在准备考研，每天图书馆泡到十点"),
    ("10002", "小鱼", "小鱼说考研数学很难，想找人一起复习"),
    ("10003", "老K", "老K想养一只橘猫，名字还没想好"),
    ("10004", "阿杰", "今天天气不错适合出去走走"),
    ("10005", "小北", "小北周末想去爬山，问有没有人一起"),
]
for qq, name, content in FACTS:
    M.remember_fact(812, qq, content, name=name, kind="note")

print("== ① 默认仍是 linear ==")
chk("默认 fusion=linear", str(M._mcfg().get("fusion")) == "linear", M._mcfg().get("fusion"))
lin = M.recall(812, "考研", limit=10, bump=False)
chk("线性检索命中考研", lin and any("考研" in x["content"] for x in lin[:2]),
    [x["content"][:12] for x in lin])
lin_ids = [x["id"] for x in lin]

print("== ② RRF 模式 ==")
_CFG["memory"]["fusion"] = "rrf"
rrf = M.recall(812, "考研", limit=10, bump=False)
chk("RRF 命中考研且排前", rrf and any("考研" in x["content"] for x in rrf[:2]),
    [x["content"][:12] for x in rrf])
chk("分数归一化到 0~1（recall_min 仍可用）",
    all(0.0 <= x["score"] <= 1.0 for x in rrf) and max(x["score"] for x in rrf) > 0.99,
    [x["score"] for x in rrf])
chk("无关查询不硬凑", M.recall(812, "显卡驱动", limit=10, bump=False) == [])

print("== ③ 动态阈值 ==")
# 构造 6 条：一条三信号都靠前，其余各强一项，确认阈值收缩但 ≥4 条
fake = []
for i in range(6):
    row = [i, "", "", "", "", 1000 - i]      # 与真实 rows 同形：id 在 0、ts 在 5
    fake.append((0.5, 0.9 if i == 0 else 0.1, 1.0 if i == 1 else 0.0,
                 0.9 if i == 2 else 0.3, row, "测试"))
fused = M._rrf_rerank(fake, {"rrf_k": 60, "rrf_dynamic": True, "rrf_min_results": 4})
chk("动态阈值后仍 ≥4 条", len(fused) >= 4, len(fused))
chk("多信号都靠前的排最前", fused[0][4][0] == 0, fused[0][4][0])
fused_all = M._rrf_rerank(fake, {"rrf_k": 60, "rrf_dynamic": False})
chk("关掉动态阈值则全保留", len(fused_all) == 6, len(fused_all))

print("== ④ 切回 linear 恢复 ==")
_CFG["memory"]["fusion"] = "linear"
lin2 = M.recall(812, "考研", limit=10, bump=False)
chk("切回线性后结果一致", [x["id"] for x in lin2] == lin_ids,
    ([x["id"] for x in lin2], lin_ids))

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
