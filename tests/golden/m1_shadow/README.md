# M1 影子核心证据（2026-11-05）

本目录由 `tests/golden/run_shadow_m1.py` 生成，**不参与线上消息链路**。

| 目录 | 内容 | 说明 |
|---|---|---|
| `fake/` | 全 240 条 Golden Set，假模型 | 只证明 Turn → Core → 决策/日志管线对每条样本都能完成，**不代表决策质量** |
| `local/` | 按 category 分层抽样的真本地模型回放 | 延迟/动作/差异/抽样复核证据；模型为 `ollama_fast`，零 API 费用 |

## 复现

```bash
python tests/golden/run_shadow_m1.py --fake
python tests/golden/run_shadow_m1.py --limit 72 --workers 2
```

## 边界

- 只读 `tests/golden/golden_set.json`；不发 QQ 消息、不执行工具、不写生产 state。
- 真模型回放只允许本机 Ollama；`bot._shadow_model` 硬拒绝云端提供方，M1 影子成本上限为 0。
- 假模型结果中的 `forbidden_hit` 是“假模型一律 silent”造成的，不能用于评价 M1 判断质量。
