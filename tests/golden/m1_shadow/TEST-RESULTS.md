# M1 测试结果（2026-11-05）

基线：M0 tag `golden-m0-base-2026-11-05`（`14c089e`）  
ADR：`docs/架构决策-统一智能核心-2026-11-05.md`（v1.0，`810d62a`）

## 1. M1 专项

```bash
python tests/_tshadow.py
python tests/_tshadow_entry.py
```

结果：两项均 **PASS**。

`_tshadow.py` 覆盖：Turn 确定性/冻结、MediaRef 只存引用、严格 JSON 解析、坏 JSON 保留 invalid、Policy 权限/危险工具/测试静音、Core 延迟与字符数、模型失败 error、队列覆盖率、队列满丢弃不被掩盖、一键关闭、不发送/不执行工具。

`_tshadow_entry.py` 覆盖：生产配置缺省关、env 开/关、私聊/群@/群潜水三种入口在影子开/关时旧路径发送逐字节一致、影子判 reply 也不发送、发送观测与差异分类。

## 2. 全量 SAFE：69 / 69

```bash
python tests/run_tests.py
```

结果：**69 / 69 通过，耗时 230.8s，退出码 0**。

新增两项已注册：

```text
[PASS] _tshadow.py        0.5s  RESULT: PASS
[PASS] _tshadow_entry.py  0.8s  RESULT: PASS
```

M0 Golden 守卫仍通过：

```text
[PASS] golden/_tgolden.py 0.2s  RESULT: PASS（240 条样本，sha256 cfd14bbe83fb…）
```

## 3. Golden 回放

### 3.1 假模型全量（只证明管线）

```bash
python tests/golden/run_shadow_m1.py --fake
```

- 范围：全部 240 条
- 覆盖率：240/240，错误 0
- 模型：假模型（一律 silent，**不代表决策质量**）
- 证据：`fake/summary.json` / `fake/summary.md` / `fake/result.jsonl`

### 3.2 真本地模型分层抽样

```bash
python tests/golden/run_shadow_m1.py --limit 72 --workers 2
```

- 模型：`huihui_ai/qwen2.5-vl-abliterated:3b`（本机 Ollama，0 元）
- 范围：按 category 分层 72 条（chat 11、failure 11、memory 10、multi 10、permission 10、tool 10、vision 10）
- 覆盖率：72/72，错误 1（坏 JSON）
- 延迟：p50 1,002ms / p95 22,791ms / min 568ms / max 83,276ms / mean 6,285ms
- 动作：reply 53、silent 10、tool_call 6、clarify 2、refuse 1
- 差异：match_expected 61、forbidden_hit 10、invalid 1、其它 0
- 证据：`local/summary.json` / `local/summary.md` / `local/result.jsonl` / `local/decisions.jsonl`

分类规则变更后可离线重算，不重复调模型：

```bash
python tests/golden/run_shadow_m1.py --reanalyze
```

## 4. 隔离与生产状态

- `config.json` sha256：
  `0edf7eab45e5980731f93a1ac1978942d06462edbe47400cd7d7aa622bab0f85`，**未变化**。
- `tests/_tstateiso.py`：mood/slang/expression/timing/purpose/draw_time 等生产 state hash 全部未变；
  `tests/_tisolation.py`：`qbot_shadow.DECISIONS_FILE` 已登记且测试态落临时目录。
- 全量 SAFE 中 `_tstateiso.py`、`_tisolation.py`、`_ttestmute.py` 均 PASS。
- 本报告与回放不发送真实 QQ 消息、不执行工具、不写生产记忆。

## 5. 生产代码 diff

```bash
git diff --stat
```

```text
bot.py               | 101 ++++++++++++++++++++++++++++++++++++++++++++++++++
config.example.json  |   5 +++
tests/_tisolation.py |   1 +
tests/_tstateiso.py  |   2 +
tests/_tu12b.py      |  38 ++++++++++++++++---
tests/run_tests.py   |   3 ++
```

新增：`qbot_turn.py`、`qbot_core.py`、`qbot_shadow.py`、M1 测试与 `tests/golden/m1_shadow/`。
生产 `config.json` 与 `state/` 未改；ADR-001 未改。

## 6. 结论

M1 的 5 条验收证据见 `docs/reports/M1-影子核心-2026-11-05.md`。不启动 M2。
