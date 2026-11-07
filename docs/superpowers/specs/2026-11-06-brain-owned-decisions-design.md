# 决策层收口与固定话术删除：短设计

## Intent / why

主人明确要求：把可由大脑决策层决定的东西与现有工具层屎山代码比对，删除重复、冲突流程；删除所有固定人机发言，改为大脑自己组织自然语言。此前在旧路由前叠加规则、闸门和提示词补丁的做法已被明确否定。

## Goals

- 一轮消息只由已有生产主脑 `ai_reply()` → `brain_with_tools()` 决定是否回复、追问、沉默、调用工具。
- 删除 M4 的第二次意图判断、TLS 抑制旧路由、拼图垫片与硬编码询问/失败话术。
- 工具层只执行、返回结构化结果；用户可见自然语言只从主脑出口表达。
- 删除用户点名的固定话术及同一链路的其余机器人播报/受理/失败/完成模板。

## Non-goals / anti-goals

- 不新增规则、正则、闸门、prompt clause、golden case、AST lock、wrapper 或评分体系。
- 不启用 `arch.draw=core`，不改 `config.json`，不推送远端，不写生产记忆，不真发 QQ。
- 不动 ADR-001，不重编号阶段，不启动 M5/M6。
- 不删除显式协议命令、群管权限、安全审计、资源闸；它们不是自然语言决策。

## Constraints

- ADR-001 §3.2/§3.3/§4.5 已规定：Agent Core 唯一判断意图；Tool Registry 不解释自然语言、不拼人格话术；任何工具不得直接产生用户可见消息；Renderer 是发送出口。
- 当前 `ai.omni_brain=True`、`ai.provider=custom`、`arch.draw=legacy`；已有 `brain_with_tools()` 带全套工具并收 `ToolResult`。
- 异步出图 worker 没有当前轮主脑上下文；删除固定 caption 后，只发送媒体本身，不伪造主脑语言。
- 用户已说「不要再向用户提问」；本设计按这一授权直接执行。

## Approaches

1. **保留旧路由，逐条加规则压制冲突**：已实践并失败；会产生多回复、拼图误判和更多人机话术。拒绝。
2. **另建统一 Renderer 与完整 ToolResult 迁移**：属于 M5/未排期的执行权接管，规模过大且会改动 ADR 阶段边界。拒绝。
3. **删除重复判断包装，复用现有 `brain_with_tools` 决策，工具层固定话术全删**：最小、直接满足本次要求，保留现有权限/协议硬轨。采用。

## Design

- `_handle_message` 保留协议命令、权限/资源闸、显式工具口令；删除 `_route_draw_core` 及其所有辅助、`_CORE_TLS` 抑制和 M4 模块/测试/文档入口。
- 普通自然语言消息落到 `_route_brain_reply` → `ai_reply` → `brain_with_tools`，由主脑决定 reply / ask / silent / tool_call；`send_bubbles` 是现有自然语言出口。
- `draw_submit`、`handle_moss_edit`、`handle_moss_reverse`、`do_draw` 等工具层删除 ack/caption/note/失败播报；返回 `ok/error/note` 结构，不直接发话。
- 删除 `_omni_fallback_say()` 与空回复补话；工具已执行但主脑无正文时保留结果，不伪造自然语言。
- 显式管理命令回执、权限拒绝、安全审计保留；这些是协议/安全硬轨，不是机器人生成式发言。

## What is left to the builder

- 精确删除清单、最小源码级失败断言、删除后的 SAFE 回归与旧断言清理。
- 独立分支提交，不推送、不合并、不切换生产配置。
