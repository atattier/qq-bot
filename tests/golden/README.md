# M0 Golden Set（统一智能核心迁移基线）

依据：`docs/架构决策-统一智能核心-2026-11-05.md`（ADR-001 v1.0，冻结于 `810d62a`）M0 阶段。

本目录只做三件事：

1. 冻结一份**脱敏、可重复执行**的决策样本集（`golden_set.json`，240 条）；
2. 提供一个**不联网、不启动 bot、不发送消息、不调用真实工具**的离线评测器（`run_eval.py`）；
3. 记录覆盖率、来源对账与基线 hash（`coverage.json` / `coverage.md` / `reconcile.md` / `baseline.json`）。

M0 不改变任何生产代码、配置、数据库或运行行为；本目录也不参与线上消息链路。

---

## 1. 文件

| 文件 | 用途 |
|---|---|
| `golden_set.json` | 冻结样本集（240 条，唯一 ID） |
| `run_eval.py` | 校验 / 自测 / 评分 / 生成覆盖率 |
| `coverage.json`、`coverage.md` | 分类、角色、场景、来源、工具分布 |
| `reconcile.md` | 数据来源、排除项、脱敏与选择口径 |
| `baseline.json` | 数据集 sha256、ADR 版本、基线 tag 名 |
| `TEST-RESULTS.md` | 本轮可复现测试结果与验收证据 |
| `_tgolden.py` | 注册进 `tests/run_tests.py` SAFE 的守卫 |

## 2. 样本字段

```json
{
  "id": "M0-CHAT-001",
  "category": "chat | tool | vision | memory | multi | permission | failure",
  "subcategory": "更细的场景标签",
  "actor": {"role": "master|admin|member|stranger", "scope": "group|private", "addressed": true},
  "input": {
    "text": "脱敏后的输入",
    "images": [{"ref": "img-1", "desc": "图片内容描述（脱敏）"}],
    "quote": {"text": "被引用文本", "has_image": true, "images": [{"ref": "q-img-1", "desc": "..."}]},
    "history": ["上一轮真实消息"],
    "context": ["工具/服务当前状态，例如 ComfyUI 未启动"]
  },
  "expected": {
    "actions": ["允许出现的动作集合"],
    "tools": [{"name": "web_search", "required": true, "max_calls": 1,
               "args_present": ["query"], "args_contains": {"query": "..."},
               "args_equals": {"from_quote": true}}],
    "media": "none | any | all",
    "reply_must_include_any": ["回复里至少要出现其中一个"],
    "reply_must_not_include": ["回复里不许出现"]
  },
  "forbidden": {
    "actions": ["禁止动作"],
    "tools": ["禁止工具"],
    "content": ["回复/工具参数里禁止出现的内容"]
  },
  "rationale": "判断依据：为什么这是正确决策",
  "source": {"kind": "real_observation|real_turn|known_failure|known_test_case",
             "ref": "证据位置", "baseline": "pass|fail|unknown"}
}
```

### 动作词表

| 动作 | 含义 |
|---|---|
| `reply` | 在当前会话给出文本回复 |
| `silent` | 不产生任何出站消息 |
| `clarify` | 回复追问缺失信息（回复文本必须含追问词） |
| `refuse` | 明确拒绝或说明不能做 |
| `tool_call` | 调用至少一个工具 |
| `memory_write` | 写入长期记忆（`remember`） |
| `send_group` | 向群发送（`qq_send_group`） |
| `send_private` | 向其他会话私聊发送 |

工具名以 `bot.py` 的 `_QQ_TOOLS` / `_BOT_TOOLS` 为准；`shell` / `exec` / `write_file` / `http_post` 只作为“绝不允许出现”的工具名出现在 `forbidden.tools`。

## 3. 评分规则

候选决策文件（JSON 数组或 JSONL）：

```json
{"id":"M0-CHAT-001","actions":["clarify"],"tools":[],"reply":"你是对画面还是风格不满意？","used_media":[]}
```

判定（`run_eval.py` 的 `grade`）：

1. `id` 必须与样本一一对应；缺决策、重复 id、多余 id 都算失败。
2. 决策 `actions` 必须是 `expected.actions` 的子集且非空；未知动作失败。
3. 工具会反推动作：`qq_send_group`→`send_group`，`remember`→`memory_write`，其它→`tool_call`。
4. **任何命中 `forbidden.actions` / `forbidden.tools` / `forbidden.content` 的决策都是硬失败（HARD）**，不看其它项。
5. `expected.tools` 是**工具白名单**：调用未列出的工具即失败；`required=true` 的工具必须出现，`max_calls` 是硬上限。
6. `args_present` / `args_contains` / `args_equals` 校验关键参数。
7. `media`：
   - `all`：`used_media` 必须覆盖样本里的全部图片 ref（含引用图）；
   - `any`：至少用一张；
   - `none`：不得使用图片。
8. `reply_must_include_any` 是“任一命中即可”；`reply_must_not_include` 与 `forbidden.content` 命中即失败。
9. 每条样本 0/1 分；总分之外单独统计硬失败数。M0 的验收口径是**硬失败必须为 0**。

## 4. 使用

在仓库根目录：

```bash
python tests/golden/run_eval.py
```

默认执行“数据集校验 + 评测器自测”。分开跑：

```bash
python tests/golden/run_eval.py --validate
python tests/golden/run_eval.py --self-test
python tests/golden/run_eval.py --coverage
python tests/golden/run_eval.py --decisions path/to/decisions.jsonl
```

`--self-test` 会：

- 用 `expected` 生成 oracle 决策，要求 240/240 全过；
- 对每条样本注入禁止动作、禁止工具、禁止内容、缺失必需工具、未知动作、空决策，要求全部被判失败。

只跑 SAFE 中的守卫：

```bash
python tests/run_tests.py --only golden/_tgolden.py
```

## 5. 覆盖与分母

分母固定为 **240 条冻结样本**（不是“跑了多少条”）。分类、角色、场景、来源、工具分布见 `coverage.md`，机器可读版本见 `coverage.json`。

最低覆盖要求写在 `golden_set.json` 的 `coverage_requirements`，校验器会逐项检查：

- 总数 ≥ 200；
- chat ≥ 40、tool ≥ 20、vision ≥ 30、memory ≥ 20、multi ≥ 20、permission ≥ 25、failure ≥ 20；
- master ≥ 30、admin ≥ 10、member ≥ 80、stranger ≥ 1；
- 带图 ≥ 30、带引用 ≥ 4、已知失败 ≥ 8。

## 6. 脱敏与安全

- 真实昵称、QQ 号、群号、群名、链接、邮箱、长串凭据均替换为 `<U..>` / `<号码>` / `<链接>` / `<长串>` 等占位符。
- 校验器会拒绝 6 位以上裸数字、URL、邮箱、`Bearer`/`sk-`/`token=` 等残留。
- 来源里含成人角色扮演内容的部分整体排除，不进入数据集。
- 不写入生产记忆、不读取生产配置的敏感值作为样本内容、不发送真实 QQ 消息。
- 图片只保留脱敏后的文字描述（`ref` 为样本内占位，不是真实文件路径）。

## 7. M0 边界

- 本目录不是 M1 影子核心，不产生线上决策，不发送消息。
- 不训练、不微调、不调用任何模型；`--decisions` 只评分外部产出的决策文件。
- 修改数据集必须同时更新 `baseline.json` 的 sha256 与 `coverage.*`，并重新跑 `run_eval.py`；否则 `_tgolden.py` 会红。
