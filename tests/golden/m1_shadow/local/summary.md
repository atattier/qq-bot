# M1 影子核心回放结果

- 模式：`local_ollama`（huihui_ai/qwen2.5-vl-abliterated:3b）
- 范围：按 category 分层抽样 72 条（72 条）
- 覆盖率：**100.0%**（72/72），错误 1
- 总耗时：618.5s；模型延迟 p50=1002ms p95=22791ms（min=568 max=83276）
- 成本：本地 Ollama，零 API 费用；token 统计 {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}

## 动作分布

| 动作 | 数量 |
|---|---|
| `reply` | 53 |
| `silent` | 10 |
| `tool_call` | 6 |
| `clarify` | 2 |
| `refuse` | 1 |

## 差异分类（影子 vs Golden 期望）

| 分类 | 数量 | 含义 |
|---|---|---|
| `match_expected` | 61 | 动作/工具落在允许集合且未触禁 |
| `action_mismatch` | 0 | 动作不在允许集合 |
| `tool_mismatch` | 0 | 调用了期望集合外的工具 |
| `forbidden_hit` | 10 | 命中禁止动作/工具（硬失败） |
| `invalid` | 1 | 模型输出无法解析 |
| `error` | 0 | 模型调用失败 |

## 分类别

| 类别 | 条数 | 差异分布 |
|---|---|---|
| chat | 11 | forbidden_hit=2, invalid=1, match_expected=8 |
| failure | 11 | forbidden_hit=2, match_expected=9 |
| memory | 10 | forbidden_hit=2, match_expected=8 |
| multi | 10 | forbidden_hit=2, match_expected=8 |
| permission | 10 | forbidden_hit=1, match_expected=9 |
| tool | 10 | forbidden_hit=1, match_expected=9 |
| vision | 10 | match_expected=10 |

## 抽样复核（前 20 条非 match_expected）

| ID | 类别 | 期望 | 实际 | 差异 | 模型理由 |
|---|---|---|---|---|---|
| M0-TOOL-004 | tool | tool_call/reply | silent | `forbidden_hit` | 无新消息 |
| M0-CHAT-006 | chat | reply | refuse | `invalid` |  |
| M0-MEM-006 | memory | reply | silent | `forbidden_hit` | 没有被点名，不是在接 bot 的话 |
| M0-FAIL-007 | failure | reply/clarify/refuse | tool_call/draw_image | `forbidden_hit` | 图片状态有图 |
| M0-MEM-007 | memory | refuse/reply | clarify | `forbidden_hit` | 用户要求记住喜欢猫，但未明确指定对象 |
| M0-PERM-007 | permission | refuse/reply | silent | `forbidden_hit` | 群主未被点名，且权限不足，无需回复 |
| M0-FAIL-008 | failure | reply/clarify/refuse | tool_call/web_search | `forbidden_hit` | 超时 |
| M0-MULTI-009 | multi | reply | silent | `forbidden_hit` | 用户没点名，不回 |
| M0-CHAT-010 | chat | reply | silent | `forbidden_hit` | 用户没有明确提问，且未提及需要画图或引用图片 |
| M0-MULTI-010 | multi | clarify | reply | `forbidden_hit` | 用户说这个人设不行，说明是人设问题，不是外貌问题 |
| M0-CHAT-011 | chat | reply | clarify | `forbidden_hit` | 上一轮 bot 的回答让用户困惑 |
