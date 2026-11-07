# M0 测试结果（2026-11-05）

代码基线：`a58a2be`（R4）  
ADR：`docs/架构决策-统一智能核心-2026-11-05.md`（v1.0，`810d62a`）  
数据集：`tests/golden/golden_set.json`（240 条）

## 1. M0 自身结果

```bash
python tests/run_tests.py --only golden/_tgolden.py
```

结果：**PASS / 1 个用例**，输出：

```text
RESULT: PASS（240 条样本，sha256 cfd14bbe83fb…）
```

```bash
python tests/golden/run_eval.py --validate --self-test
```

结果：校验 240/240；oracle 240/240；禁止动作/工具/内容、缺必需工具、未知动作、空决策全部被抓出。

M0 守卫只读 `tests/golden/`，不联网、不启动 bot、不发送消息、不写 state。

## 2. 全量 SAFE：67 / 67

```bash
python tests/run_tests.py
```

结果：**67 / 67 通过，耗时 208.6s，退出码 0**。

```text
[PASS] _tu12b.py         2.0s
[PASS] test_skill_edit.py 6.8s
[PASS] golden/_tgolden.py 0.3s  RESULT: PASS（240 条样本，sha256 cfd14bbe83fb…）
--------------------------------------------------------------------
全部通过：67 / 67，耗时 208.6s
EXIT:0
```

### 本轮为 SAFE 做的两处测试修复

1. `tests/_tu12b.py`：固定基线 `880211a` 早于已批准的 R2/R4，原先会把
   `chat_once_tools`、`_task_chat_with_tools`、`_looks_like_napcat` 判红。
   现在这 3 个函数单独与批准后的 `a58a2be` 做 AST 对账，其余非画图函数仍锁
   `880211a`；批准清单外的任何改动仍会失败。
2. `tests/test_skill_edit.py`：梗百科测试原来把 `qbot_memewiki` 绑到真实
   `state/memewiki`，会生成真实卡片。现在词库复制到临时目录，卡片与
   `auto.json` 全部写在临时目录，测试结束随 `tmp` 删除。

## 3. 隔离检查

- `config.json` 运行前后 sha256 均为
  `0edf7eab45e5980731f93a1ac1978942d06462edbe47400cd7d7aa622bab0f85`，未变化。
- `state/memewiki` 修复后全量 SAFE 前后各 29 个文件，**0 新增 / 0 删除 / 0 改动**。
- 修复前的第一次 SAFE（66/67）曾由旧 `test_skill_edit.py` 生成 3 张真实卡片：
  `meme_1793877269189.png`、`meme_1793877269290.png`、`meme_1793877269460.png`。
  这 3 张已移到
  `<用户目录>\AppData\Local\Temp\golden_build\leaked_cards\` 留证，未直接删除。
- `state/maimem.db`、`state/mood.json`、`state/metrics.db` 等在生产 bot 运行期间
  仍会有正常变化；不归因于测试，也不据此声称“整个 state 零变化”。
- `_tstateiso.py`、`_tisolation.py`、`_ttestmute.py` 均 PASS。

## 4. 生产代码 diff

```bash
git diff a58a2be -- bot.py config.json config.example.json state
```

为空。工作区改动只在 `tests/`：

```text
 M tests/_tu12b.py
 M tests/run_tests.py
 M tests/test_skill_edit.py
?? tests/golden/
```

## 5. 验收结论

| 条件 | 结果 |
|---|---|
| 数据集 ≥200 条 | 通过（240） |
| 脱敏且覆盖明确 | 通过 |
| 每条有预期/禁止动作 | 通过 |
| 可重复、不发真实消息 | 通过 |
| SAFE 全绿 | **通过（67/67）** |
| 生产代码 diff 为零 | 通过 |

M0 验收通过。独立提交见 `git log`，基线 tag 为
`golden-m0-base-2026-11-05`；不启动 M1。

复现命令：

```bash
python tests/golden/_tgolden.py
python tests/golden/run_eval.py --validate --self-test
python tests/run_tests.py
```
