# 第二阶段设计：未完成事项 + Reflection

> 状态：**已实施（2026-10-26 13:17~13:40）**。实现见 `qbot_reflect.py` + `tools/test_reflect.py`；
> 当前补充：切换人格后即使没有新记录也会持久化游标，避免同一条「人格变了」每轮重复重置。
> 原则：不迁移框架、不重写 bot、复用现有消息链/队列/存储/调度、保护画图/反推/Moss/表情库/多人格。

---

## 一、审计：现有实现的真实情况

### 1.1 情绪状态机（第一阶段）调用点

| 位置 | 作用 |
|---|---|
| `bot.py:12920` `qbot_mood.bind(STATE/"mood.json", CFG)` | 启动绑定 |
| `bot.py:12924` `mood_block=qbot_mood.block` | 传给 persona |
| `qbot_persona.py:213` | `build_messages` 末尾注入心情段 |
| `bot.py:11045` `qbot_mood.note(gid, text)` | 每条真人消息更新 |
| `bot.py:4367` `burst_cap(gid, burst_max)` | 影响分条数 |
| `bot.py:7077` `sim_bias(gid) < 0.25` | 影响是否主动插话 |

**结论**：情绪是**按会话 key**（群/私聊）存的，**不按人格隔离**。

### 1.2 记忆 / 聊天记录 / 持久化

```
qbot_memory.py（SQLite: state/maimem.db，148 KB）
  person(id,gid,qq,name,ts)          18 行
  fact / facts(qq,name,content,kind,ts,src)   11 行
  people(...)                        16 行
  turns(id, ts, src, who, qq, text, reply)   400 行（10-20 → 10-26，约 67 条/天）
  meta(k,v)                          schema 版本
接口：note_person / remember_fact / recall / forget / log_turn / recent_turns /
      render_person / render_recent / profile_block / stats
```

`log_turn` 只有两个调用点：`bot.py:4286`（正常回复）、`bot.py:7554`（主动插话）。
**这就是 Reflection 最理想的增量输入源** —— 已带 `src`、`ts`、`who`、`qq`。

**结论**：`turns` 是全局表，**没有 persona 列**。按它派生观点，必须自己带人格戳。

### 1.3 定时 / 后台调度

| 机制 | 位置 | 周期 | 说明 |
|---|---|---|---|
| `scheduler_loop` | `bot.py` 12848 启动 | 15s | 只处理固定时刻播报（`schedules`） |
| `idle_opener_loop` | 12786 启动 | 60s | 冷场开场，每天限额 + 时段窗口 |
| `_draw_q` / `_vg_q` 队列 | — | — | 现画/视频串行队列 |
| 各 worker 线程 | — | — | 反推、i2i、agent watchdog 等 |

**结论**：已有成熟的「60s 巡检 + 限额 + 时段窗口」范式（`idle_opener_tick`），
Reflection 照抄这个形状最省事，不要新造调度框架。

### 1.4 主动发言链路（现有两段式其实只有一段）

```
消息到达
  → sim_allowed(gid, uid)        ← 闸门：冷却 / 每小时上限 / 群活跃度 / 心情
  → 合批窗口 _sim_batch_flush    ← 6 秒或满 6 条
  → sim_join(gid, who, text...)  ← 模型单次判定「要不要接这句」，输出 [SILENT] 或一句话
  → send_bubbles
```

另有 `idle_opener_tick()`（冷场主动开新话题），配置在 **`sim.idle`**：
`minutes=30`（冷场判定）/ `max_per_day=4` / `gap_sec=3600` / `chance=0.6` /
`hours="9-24"`（时段窗口），模型可输出 SILENT 放弃。

`sim` 其余闸门实际值：`cooldown_sec=240`、`max_per_hour=4`、`min_activity=2`、`inject=6`。

**结论**：
- 「值不值得说」现在**混在模型的一次判定里**，没有独立的候选评分。
- 「适不适合说」已经由 `sim_allowed` + `idle_opener_tick` 的窗口/限额实现。
- **本阶段只补「候选池」这一层，不重写这两段。**

### 1.5 多人格隔离现状

| 数据 | 是否按人格隔离 |
|---|---|
| 人设正文 `ai.system` / `roles/*.md` | 人格本身就是它 |
| `HISTORY`（滚动历史） | ❌ 按 gid |
| `CONTEXT`（群上下文） | ❌ 按 gid |
| `qbot_memory` 全部表 | ❌ 无 persona 列 |
| `state/mood.json` | ❌ 按 gid |
| `state/subjects.json`（角色卡缓存） | ❌ 全局（但那是客观设定，可共享） |

**结论**：**今天没有任何私人状态按人格隔离**。换人格后旧的记忆/上下文仍在。
本阶段新增的 `opinion` / `open_loop` 是**最私人**的东西（"我对这事的看法"），
必须按人格隔离，否则切到「毒舌群友」会突然引用「小鲸」的柔软记忆。

### 1.6 是否已有 Reflection / 总结机制

全仓 grep `reflect|反思|summary|总结`：只有 admin 页面的 `<summary>` 折叠标签和
ComfyUI 插件的无关代码。**没有现成机制可复用，也不存在重复造轮子。**

### 1.7 与早前报告不一致之处

| 早前说法 | 真实代码 |
|---|---|
| 「主动消息拆两个决策」 | 现在是**一次模型判定**，闸门在调用前 |
| 「情绪按会话」 | ✅ 属实 |
| 「记忆跨场合」 | ✅ 属实（`render_recent(exclude_src=...)`） |
| 「人格隔离」 | **从来没有**，需要新建 |

---

## 二、Reflection 设计

### 2.1 四类产出

| kind | 定义 | basis 允许值 | 例子 |
|---|---|---|---|
| `observation` | **有来源**的观察，必须能指回原话 | `fact`（有 quote）/ `inference`（推测） | 「群友甲 这周提了三次外卡」 |
| `opinion` | bot 自己的看法/偏好，带立场 | `opinion` | 「这游戏的手把操作是真的反人类」 |
| `open_loop` | 没解决的事 | `fact` / `inference` | 「他说要试量化模型，还没试」 |
| `follow_up` | 值得以后再聊的话题 | `inference` / `opinion` | 「下次可以问问他外卡跑得怎么样」 |

**三类 basis 的硬规则**（这是防编造的核心）：

| basis | 判定 | 注入时的措辞 |
|---|---|---|
| `fact` | `refs[].quote` 必须能在 `turns.text` 里**逐字**找到 | 直接说 |
| `inference` | 允许推测，但必须标 `inference` | 「你猜/你感觉」 |
| `opinion` | 纯主观，不要求来源 | 「你觉得」 |

> **反编造闸门**：模型产出 `basis=fact` 时，程序拿 `quote` 去 `turns` 表做子串校验。
> 找不到 → 自动降级为 `inference`；再找不到任何 `refs` → **整条丢弃**。
> 这样「我今天看了个视频」这种编造的经历永远进不了 fact。

### 2.2 表结构（新增，不动现有表）

```sql
CREATE TABLE IF NOT EXISTS reflect_item(
  id         TEXT PRIMARY KEY,   -- sha1(persona|gid|kind|dedup)[:16]，确定性，天然幂等
  kind       TEXT NOT NULL,      -- observation|opinion|open_loop|follow_up
  persona    TEXT NOT NULL,      -- 所属人格（隔离键）
  gid        TEXT NOT NULL,      -- 会话 key（群号 / 私聊负数）
  src        TEXT,               -- group:123 / private:456
  topic      TEXT,               -- 2~6 字话题词，去重主键的一部分
  content    TEXT NOT NULL,      -- 一句话，给 bot 自己看的中文
  detail     TEXT,               -- 可选展开
  basis      TEXT NOT NULL,      -- fact|inference|opinion
  confidence REAL NOT NULL,      -- 0~1
  status     TEXT NOT NULL,      -- open|in_progress|blocked|resolved|dismissed
  refs       TEXT,               -- JSON [{turn_id, who, ts, quote}]
  dedup      TEXT NOT NULL,      -- kind|persona|gid|normalize(topic)
  weight     REAL DEFAULT 0.5,   -- 重要性
  created    REAL, updated REAL,
  last_used  REAL, use_count INTEGER DEFAULT 0,
  mentions   INTEGER DEFAULT 0,  -- 被主动提起过几次
  no_reply   INTEGER DEFAULT 0   -- 提起后没人接几次
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_item_dedup ON reflect_item(dedup);
CREATE INDEX IF NOT EXISTS ix_item_lookup ON reflect_item(persona, gid, kind, status);

CREATE TABLE IF NOT EXISTS reflect_cursor(
  src TEXT PRIMARY KEY,
  last_turn_id INTEGER DEFAULT 0,
  last_run REAL DEFAULT 0,
  runs INTEGER DEFAULT 0,
  fails INTEGER DEFAULT 0
);
```

放在**同一个** `state/maimem.db`（WAL 模式，和 `qbot_memory` 各自开连接，互不影响），
不新建库文件 —— 备份/迁移脚本只需管一个文件。

### 2.3 稳定 ID 与去重

```
dedup = kind | persona | gid | normalize(topic)
normalize = 去空白/标点、转小写
id    = sha1(persona|gid|kind|dedup)[:16]
```

写入用 `INSERT ... ON CONFLICT(dedup) DO UPDATE`：
- 已存在 → 只更新 `content`（取更长的）、`confidence`（取更高）、`refs`（合并去重）、`updated`
- **不新建行** ⇒ 同一件事聊十次也只有一条

`open_loop` 的 `topic` 由模型给（如「量化模型」「外卡」），比整句去重稳得多。

---

## 三、未完成事项（open_loop）生命周期

| 状态 | 进入条件 | 退出条件 | 更新方式 |
|---|---|---|---|
| `open` | 模型判定「这事没完」且过了反编造闸门 | 被引用 → `in_progress`；用户明说完成 → `resolved`；用户说不想聊 → `dismissed` | 新 observation 命中同 topic 时刷新 `updated`/`weight` |
| `in_progress` | 被主动提起过（`use_count++`）或被用户接话 | 同上 | `mentions++` |
| `blocked` | 连续 3 次被闸门挡下（心情/时段/冷却），或模型标 blocked | 条件解除后回到 `open` | 每次尝试被挡 `no_reply++` |
| `resolved` | ① 用户明确说「弄完了/解决了」② 新的 fact 覆盖它 | 终态，**不再出现在候选** | 保留记录可回溯 |
| `dismissed` | ① 用户说「别管这个」② 提起 3 次都没人接 | 终态 | 同上 |

**防重复提起**：
- 候选查询排除 `status IN (resolved, dismissed)`
- 同一 `open_loop` 两次提起间隔 ≥ `min_repeat_hours`（默认 24h）
- `no_reply >= 3` → 自动 `dismissed`（说明这事没人在意）

**长期未解决不删**：
- 不按时间删除任何行
- `weight` 随时间缓慢衰减（半衰期 14 天），降到 `weight < 0.15` 时**休眠**（仍可被关键词命中唤醒），不删除

---

## 四、调度与主动发言接口

### 4.1 Reflection 只产候选，不发消息

```
reflect_loop（独立线程，60s tick）
  ├─ 遍历 reflect.enabled 的 src
  ├─ 触发判定（见 4.2）
  ├─ 取增量 turns（cursor 之后，最多 max_turns 条）
  ├─ 本地预筛（不调模型，见 5.1）
  ├─ 通过才调一次模型 → JSON 产出
  ├─ 反编造校验 → 入库（幂等）
  └─ 推进 cursor
```

**这条链里没有任何 send_* 调用。** 验收项之一就是 grep 不到发送函数。

### 4.2 触发条件（三选一）

| 条件 | 默认 |
|---|---|
| a. 新增 turns ≥ `min_turns` 且距上次 ≥ `min_interval` | 30 条 / 30 分钟 |
| b. 距上次 ≥ `max_interval` 且新增 ≥ `min_new` | 6 小时 / 5 条 |
| c. 管理员口令「反思」 | 手动 |

### 4.3 候选 → 主动发言

```
reflect_candidates(gid, persona, limit=5)
  score = weight × recency_factor × (1 - min(use_count/3, 0.8))
          × curiosity_bonus(情绪)        ← 只加权，不改数据
  过滤：status ∈ (open, in_progress)、未过期、间隔够

  ↓ 交给现有链路
① 值不值得说：score ≥ speak_threshold（默认 0.45）
② 现在适不适合说：sim_allowed(gid) 的全部闸门
   + qbot_mood.sim_bias(gid) ≥ 0.25
   + idle 时段窗口（复用 idle_opener_tick 的 quiet_hours）
   + agent 不在跑（agent_working）
```

**情绪的作用边界（严格）**：
- `curiosity` 高 → 候选里偏 `follow_up`
- `irritation` 高 / `energy` 低 → `sim_bias` 归零，**不发**
- 情绪**不修改** item 的 `content` / `status` / `weight`（只影响打分和是否开口）

**不得因定时触发就强制发**：`reflect_loop` 只入库；主动发言走 `idle_opener_tick`
的同一套限额（`sim.idle`：每天 4 次 + 间隔 3600s + 时段 9-24 + chance 0.6 + 模型可 SILENT）。

---

## 五、资源控制

### 5.1 本地预筛（调模型之前）

逐条打分，取 top `max_input`（默认 40）：
- 丢弃：纯表情/图片占位、长度 < 4、与上一条重复度 > 0.8
- 加分：含问号、含「以后/下次/明天/改天」、含「没/不/还没」、长度 20~200、被多人回复

### 5.2 频次与并发

| 项 | 默认值 |
|---|---|
| 单 src 最短间隔 | 30 分钟 |
| 单 src 每天最多 | 12 次 |
| 全局并发 | 1（`_reflect_busy` 标志，不排队） |
| 单次输入上限 | 40 条 turns（约 2~4k tokens） |
| 单次输出上限 | 1500 tokens |
| 失败重试 | 最多 3 次，退避 5/15/60 分钟；连续 3 次失败退避 30 分钟 |

### 5.3 日志

- 每次运行一行：`reflect src=... 新增N 候选M 入库K 耗时Xs via=...`
- 丢弃原因计数（低价值/反编造失败/重复）
- 失败打 WARNING，不刷屏（同类错误 5 分钟内只打一次）

---

## 六、最小改造方案

### 6.1 新增文件

| 文件 | 作用 | 行数估计 |
|---|---|---|
| `qbot_reflect.py` | 表结构、入库/去重、候选查询、生命周期 | ~350 |
| `docs/reflection-design.md` | 本文 | — |

### 6.2 改动现有文件（尽量小）

| 文件 | 改动 | 风险 |
|---|---|---|
| `bot.py` | ① import + `bind` ② 新增 `reflect_loop` 线程（照抄 `idle_opener_loop` 形状）③ `idle_opener_tick` 里取候选（可选）④ 口令「反思」⑤ 旋钮 `reflect.*` | 低：新线程独立，异常自己吞 |
| `qbot_persona.py` | 在 `build_messages` 里注入「未完成事项」段（**可选，二期**） | 中：影响所有回复的 prompt |

**一期不做 prompt 注入** —— 先只入库 + 主动发言用。理由：注入会影响每条回复，
出问题难回滚；先让数据跑起来，确认质量再决定要不要喂进人设。

### 6.3 调用链（完整）

```
收消息 → log_turn 写入 turns（已有，不动）
                    ↓
        reflect_loop（新，60s）
                    ↓ 增量读 turns
              本地预筛 → 模型 → 反编造校验 → reflect_item 入库
                    ↓
        idle_opener_tick（已有，60s）
                    ↓ 有空位时
             取候选（新）→ 现有闸门（sim_allowed + mood + 时段）
                    ↓ 全过
                 send_bubbles（已有）
```

### 6.4 配置项（新增 `reflect` 段）

```json
"reflect": {
  "enabled": true,
  "min_turns": 30, "min_interval": 1800, "max_interval": 21600, "min_new": 5,
  "max_input": 40, "max_output_tokens": 1500, "daily_max": 12,
  "speak_threshold": 0.45, "min_repeat_hours": 24,
  "provider": "studio"
}
```

---

## 七、风险与回滚

| 风险 | 表现 | 缓解 |
|---|---|---|
| 污染记忆库 | 编造的"经历"进了 fact | 反编造闸门（quote 子串校验）+ 独立表，不动 `facts` |
| 重复记录 | 同一件事几十条 | `dedup` 唯一索引 + `ON CONFLICT DO UPDATE` |
| 人格串味 | 换人格后引用旧看法 | `persona` 列强制过滤；候选查询必带 persona |
| 模型乱编 | 生成不存在的对话 | 无 refs 直接丢弃 |
| token 花超 | 频繁总结 | 预筛 + 30 分钟间隔 + 每天 12 次上限 + 全局并发 1 |
| 主动发言变烦 | 每天追着说 | 复用 `sim.idle` 每天 4 次限额（现网实际值），不新开额度 |
| 影响现有功能 | 画图/反推/Moss 被拖慢 | 独立线程 + 独立队列；异常全捕获；不碰 `_draw_q`/`_vg_q` |

### 回滚（三级，都是一句话）

1. **停功能**：`reflect.enabled = false` → 线程空转，零副作用
2. **停线程**：注释 `bot.py` 里 `reflect_loop` 的 start 一行，重启
3. **清数据**：`DROP TABLE reflect_item, reflect_cursor`（独立表，不影响 `turns`/`facts`）

---

## 八、验收计划（对照 10 条）

| # | 验收项 | 验证方法 |
|---|---|---|
| 1 | 同一对话重复处理不产生大量重复 | 同一段 turns 跑两次，`COUNT(*)` 不变 |
| 2 | 每条观察/观点可追溯来源 | 抽样 10 条，`refs[].turn_id` 都能在 `turns` 里查到且 quote 是子串 |
| 3 | 虚构内容不变成真实经历 | 构造一句"我今天看了视频"喂进去，断言 basis≠fact 或整条被丢 |
| 4 | 已解决事项不再作为未完成出现 | 标 `resolved` 后查候选，断言查不到 |
| 5 | 长期未解决的有价值事项保留 | 把 `updated` 改成 30 天前，断言仍在表里（可能休眠） |
| 6 | 不同人格不共享私人状态 | A 人格写入 opinion，切到 B 查候选，断言 0 条 |
| 7 | Reflection 不直接发消息 | `grep -n "send_" qbot_reflect.py` 应为空 |
| 8 | 主动发言遵守现有冷却/安静/情绪 | 心情设为烦躁，断言 `sim_bias=0` → 不开口 |
| 9 | 重启后素材仍在 | 重启 bot，查 `reflect_item` 行数不变 |
| 10 | 现有功能不受影响 | 回归：画图 / moss反推 / 表情 / 切人格 各跑一次 |

---

## 九、待确认的三个决策点

1. **要不要一期就注入 prompt？**
   我建议**不要**。先只入库 + 主动发言用，观察一周质量再决定。
2. **`opinion` 是否跨会话共享？**
   同一人格在不同群的观点，我倾向**不共享**（`gid` 也是隔离键），否则 A 群的吐槽会跑到 B 群。
3. **`follow_up` 和 `open_loop` 要不要合并？**
   我倾向保留两类：`open_loop` 是"事情没完"，`follow_up` 是"话题可以接着聊"，
   生命周期不同（前者有 resolved，后者只有 used/dismissed）。

---

**以上为当初设计稿；实现已落地并通过专项验收。最新游标持久化补丁见文档开头状态行。**
