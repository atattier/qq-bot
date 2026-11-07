# M2 Context Assembler 回放结果

- 样本：Golden Set 240 条（只组装，不调模型）
- 完成：240；异常：0；耗时 1.7s
- 上下文预算：24000 字；实际 p50=9799 / mean=9843 / max=10682
- 超预算：0 条（关键段被丢：无）
- 告警：无
- 带图样本：32；媒体段命中：32

## 18 段覆盖与来源

| 段 | 优先级 | 关键 | 来源 | 出现 | 覆盖率 | 状态分布 |
|---|---:|---|---|---:|---:|---|
| `hard_rules` 铁律与时间 | 0 | 是 | qbot_persona.BASE_RULES + now | 240 | 100.0% | included=240 |
| `persona` 人格卡 | 1 | 是 | qbot_persona.system_prompt | 240 | 100.0% | included=240 |
| `identity` 说话人/主人身份 | 2 | 是 | bot._identity_block | 240 | 100.0% | included=240 |
| `session_policy` 会话策略 | 3 | 是 | qbot_session/CFG | 240 | 100.0% | included=240 |
| `quote` 引用资料 | 4 | 是 | Turn.quote | 4 | 1.7% | empty=236, included=4 |
| `media` 媒体引用 | 5 | 否 | Turn.media + 最近 Turn | 32 | 13.3% | empty=208, included=32 |
| `tools` 工具目录 | 6 | 否 | ToolSpec 目录 | 240 | 100.0% | included=240 |
| `history` 对话历史 | 7 | 否 | HISTORY / Turn | 125 | 52.1% | empty=115, included=125 |
| `recent_context` 最近群聊 | 8 | 否 | bot.context_block | 0 | 0.0% | empty=240 |
| `memory_person` 人物账本 | 9 | 否 | qbot_memory.render_person | 0 | 0.0% | empty=240 |
| `memory_recall` 相关旧事 | 10 | 否 | qbot_memory.recall_block | 0 | 0.0% | empty=240 |
| `memory_card` 记忆卡 | 11 | 否 | qbot_memory_card.render_block | 0 | 0.0% | empty=240 |
| `slang` 黑话/语境释义 | 12 | 否 | qbot_slang | 0 | 0.0% | empty=240 |
| `expression` 表达习惯 | 13 | 否 | qbot_expression | 0 | 0.0% | empty=240 |
| `mood` 心情 | 14 | 否 | qbot_mood.block | 0 | 0.0% | empty=240 |
| `skills` 技能说明 | 15 | 否 | qbot_skills.prompt_block | 240 | 100.0% | included=240 |
| `external_evidence` 外部证据 | 16 | 否 | memewiki/linkprev/search | 1 | 0.4% | empty=239, included=1 |
| `task_directive` 本轮硬要求 | 17 | 是 | bot.turn_directive | 14 | 5.8% | empty=226, included=14 |
