# 决策层收口与固定话术删除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除会与主脑决策重复或冲突的工具层流程及固定人机话术，让一轮消息只有一个大脑判断、一个自然语言出口；旧链路降为纯工具执行。

**Architecture:** 保留 `ai_reply()` → `brain_with_tools()` 这条已有生产主脑链路；删除 M4 在 `_handle_message` 前另开一次意图判定、用 TLS 开关压制旧路由、以及询问/失败时直接发话的代码。保留显式协议命令、权限与资源硬轨；工具执行结果通过既有 `ToolResult`/`send_bubbles` 回给主脑组织自然语言。出图 worker 只发送媒体，不再生成播报模板。

**Tech Stack:** Python 3、标准库、现有 bot.py / qbot_draw_body.py / tests/run_tests.py。

**Spec:** `docs/superpowers/specs/2026-11-06-brain-owned-decisions-design.md`

## Global Constraints

- 不新增规则、正则、闸门、prompt clause、golden case、AST lock 或 wrapper。
- 不启用 `arch.draw=core`；不改 `config.json`；不推送远端。
- 不改 ADR-001；不把清理重新编号为 M4/M5/M6。
- 不改显式协议命令、群管权限、权限拒绝、资源闸与安全审计。
- 不真发 QQ 消息、不真执行出图、不写生产记忆。
- 固定话术全部删除；不以新模板替换。
- 生产用户可见自然语言由 `chat()` / `brain_with_tools()` / `ai_reply()` 组织。

## Review Focus

- 显式管理命令（如 `/status`、画图开关）保留硬执行和结构化回执；若误删会让管理员失去控制面——由 Task 1 的协议命令未受影响断言覆盖。
- 权限与资源闸（未点名不得画、群友不得用主人工具）保留；若被主脑绕过会越权执行——由 Task 1 的权限工具面断言覆盖。
- 异步出图完成时没有当前轮 `chat()` 可复用；若仍发模板会违背用户要求，若完全不回执则结果丢失——由 Task 2 的媒体-only 发送断言覆盖。
- 工具失败返回结构化错误；若直接 `send_group(msg)` 会绕过主脑——由 Task 2 的无文字发送断言覆盖。
- 主脑工具循环使用了工具却空回复；若补固定兜底话术会重现人机口吻——由 Task 2 的空回复不造话断言覆盖。

---

### Task 1: 删除决策层外包与 M4 二次判断

**Files:**
- Modify: `bot.py:16013-16215`, `bot.py:16453`, `bot.py:16633-16637`, `bot.py:16223-16224`, `bot.py:16316-16317`, `bot.py:16333-16334`
- Delete: `qbot_draw_intent.py`, `qbot_turn.py` 仅当确认无其它使用者
- Test: `tests/_tcleanup.py`；从 `tests/run_tests.py` 移除 M4 专用 SAFE

**Interfaces:**
- Consumes: `_handle_message(...)`, `ai_reply(...)`, `brain_with_tools(...)`
- Produces: `_handle_message` 不再调用 `_route_draw_core`；`_core_draw_chat()` 不再存在；`arch.draw` 不再影响运行路径。

- [ ] **Step 1: Write the failing test**

在 `tests/_tcleanup.py` 断言源码中不存在 `_route_draw_core(`、`_core_draw_chat`、`_CORE_TLS`、`qbot_draw_intent`、`QBOT_ARCH_DRAW`、`compose_refs`；断言 `_handle_message` 仍调用 `_route_brain_reply`；断言 `_route_admin_slash`、`_omni_tools`、`_exec_bot_tool` 仍存在。运行前注册到 `tests/run_tests.py`。

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/_tcleanup.py`
Expected: FAIL，报告 M4 外包仍存在。

- [ ] **Step 3: Delete the duplicate decision layer**

删除 `bot.py` 中 M4 核心判断函数、TLS 抑制与所有 `_core_draw_chat()` 调用及 `_handle_message` 注入；删除 `qbot_draw_intent.py`、`qbot_turn.py` 与 M4 专用测试/文档入口。保留现有 `ai_reply` → `brain_with_tools` 决策链及全部权限/资源硬轨。不要新增替代判断函数。

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/_tcleanup.py`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove duplicate draw decision layer"
```

### Task 2: 删除固定话术并收归主脑自然语言

**Files:**
- Modify: `bot.py:3395`, `bot.py:3441`, `bot.py:16979`, `bot.py:6594-6595`, `bot.py:6603-6615`, `bot.py:16119`, `bot.py:16198`, `bot.py:16204`, `bot.py:16248-16249`, `bot.py:16283-16284`, `bot.py:16407`, `bot.py:7955-7956`, `bot.py:15987-15989`
- Modify: `qbot_draw_body.py:1442-1461`, `qbot_draw_body.py:1964`, `qbot_draw_body.py:1973-1984`, `qbot_draw_body.py:1991-2001`, `qbot_draw_body.py:912`
- Modify: `qbot_draw_body.py:332-350`, `bot.py:3327-3350`, `bot.py:3365-3397`, `bot.py:3411-3443`（工具层固定失败话术改为结构化返回）
- Test: `tests/_tcleanup.py`, `tests/_tdraw.py`, `tests/_tentry.py`, `tests/_tomni.py`, `tests/run_tests.py`

**Interfaces:**
- Consumes: `draw_submit(...) -> (ok, message)`, `do_draw(job)`, `send_drawn(gid, path, caption="", reply_to=0)`
- Produces: `draw_submit(..., ack=False, ack_text=None)` 不直接发送；`do_draw()` 成功/失败只走媒体或结构化返回；`brain_with_tools()` 空回复不造模板。

- [ ] **Step 1: Extend the failing test**

断言 `qbot_draw_body.py` 的 `draw_submit` 与 `do_draw` 函数体内没有 `send_group(`、`send_drawn(...caption...)`、`caption =`、`ack_text` 分支；断言 `bot.py` 指定 M4/画图函数体内没有 `send_group(`；断言固定短语全部从 `.py` 源文件中消失；保留 `send_bubbles` 和既有 `ToolResult` 返回。

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/_tcleanup.py`
Expected: FAIL，列出仍存在的固定发送点和固定短语。

- [ ] **Step 3: Remove fixed utterances, preserve tool results**

删除 `draw_submit` 的 ack 发送分支和默认 ack 值改为 `False`；删除所有 `ack_text=` 实参。`do_draw` 成功只发图片段，失败不直接 `send_group`，把结构化错误留作工具返回值；删除完成 caption、反推框数 note、受理/失败模板、`_omni_fallback_say()` 调用及其固定文本。`ai_reply` 的后端异常不再直接固定回复；M4 删除后的残留固定询问/失败讲话一并删掉。协议命令、权限拒绝和安全审计保留。

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/_tcleanup.py`
Expected: PASS。

- [ ] **Step 5: Run the suite and fix only direct deletions**

Run: `python tests/run_tests.py`
Expected: 全部通过；失败若来自已删除行为的旧断言，同步删除或改为「不再发生」断言，不新增规则。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove hardcoded tool utterances"
```
