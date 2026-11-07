# M1 影子核心回放结果

- 模式：`fake`（fake）
- 范围：全部 Golden Set（240 条）
- 覆盖率：**100.0%**（240/240），错误 0
- 总耗时：0.0s；模型延迟 p50=0ms p95=0ms（min=0 max=0）
- 成本：假模型，零费用；token 统计 {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}

## 动作分布

| 动作 | 数量 |
|---|---|
| `silent` | 240 |

## 差异分类（影子 vs Golden 期望）

| 分类 | 数量 | 含义 |
|---|---|---|
| `match_expected` | 66 | 动作/工具落在允许集合且未触禁 |
| `action_mismatch` | 0 | 动作不在允许集合 |
| `tool_mismatch` | 0 | 调用了期望集合外的工具 |
| `forbidden_hit` | 174 | 命中禁止动作/工具（硬失败） |
| `invalid` | 0 | 模型输出无法解析 |
| `error` | 0 | 模型调用失败 |

## 分类别

| 类别 | 条数 | 差异分布 |
|---|---|---|
| chat | 56 | forbidden_hit=27, match_expected=29 |
| failure | 25 | forbidden_hit=25 |
| memory | 25 | forbidden_hit=25 |
| multi | 29 | forbidden_hit=15, match_expected=14 |
| permission | 37 | forbidden_hit=33, match_expected=4 |
| tool | 24 | forbidden_hit=23, match_expected=1 |
| vision | 44 | forbidden_hit=26, match_expected=18 |

> 假模型一律返回 silent；本表差异分类只证明管线覆盖，不能用于评价决策质量。

## 抽样复核（前 20 条非 match_expected）

| ID | 类别 | 期望 | 实际 | 差异 | 模型理由 |
|---|---|---|---|---|---|
| M0-CHAT-001 | chat | clarify/reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-002 | chat | clarify/reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-003 | chat | clarify/reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-004 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-005 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-006 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-007 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-008 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-009 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-010 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-011 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-015 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-018 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-028 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-031 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-032 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-033 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-036 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-038 | chat | reply | silent | `forbidden_hit` | fake replay |
| M0-CHAT-042 | chat | reply | silent | `forbidden_hit` | fake replay |
