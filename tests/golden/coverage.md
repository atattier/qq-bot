# M0 Golden Set 覆盖率

分母：**240 条冻结样本（M0 Golden Set v1）**。

## 分类

| 分类 | 条数 | 占比 |
|---|---:|---:|
| chat | 56 | 23.3% |
| tool | 24 | 10.0% |
| vision | 44 | 18.3% |
| memory | 25 | 10.4% |
| multi | 29 | 12.1% |
| permission | 37 | 15.4% |
| failure | 25 | 10.4% |

## 角色 / 场景

| 维度 | 值 | 条数 | 占比 |
|---|---|---:|---:|
| 角色 | admin | 11 | 4.6% |
| 角色 | master | 110 | 45.8% |
| 角色 | member | 114 | 47.5% |
| 角色 | stranger | 5 | 2.1% |
| 场景 | group | 165 | 68.8% |
| 场景 | private | 75 | 31.2% |

## 来源与基线

| 维度 | 值 | 条数 | 占比 |
|---|---|---:|---:|
| 来源 | known_failure | 15 | 6.2% |
| 来源 | known_test_case | 119 | 49.6% |
| 来源 | real_observation | 106 | 44.2% |
| 来源 | real_turn | 0 | 0.0% |
| 基线 | fail | 24 | 10.0% |
| 基线 | pass | 213 | 88.8% |
| 基线 | unknown | 3 | 1.2% |

带图样本：30；带引用样本：4；被点名样本：75。

## 必需工具分布

- `recall_memory`：14
- `web_search`：13
- `draw_image`：12
- `draw_like_this`：9
- `remember`：5
- `qq_send_group`：4
- `qq_history`：3
- `reverse_prompt`：3
- `qq_members`：2
- `bot_status`：2
- `qq_groups`：1
- `cloud_draw`：1
- `send_meme`：1

## 已知失败样本

共 15 条：M0-CHAT-001、M0-CHAT-002、M0-CHAT-003、M0-CHAT-004、M0-CHAT-005、M0-CHAT-006、M0-CHAT-007、M0-CHAT-008、M0-CHAT-009、M0-CHAT-010、M0-CHAT-011、M0-TOOL-020、M0-TOOL-021、M0-VIS-022、M0-VIS-023
