# open-claude-ui 研究 → /admin 网页端改版（2026-10-17）

仓库：<https://github.com/Caho1/open-claude-ui>（MIT，Claude 桌面端逆向还原：30 个 React 组件 + 8 类交互物理算法 + 3000 行手册）

## 0. 一句话结论

仓库本体是 **React + TypeScript + Tailwind** 组件库，直接搬不进本项目这种「单文件、零外部依赖、CDN 打不开」的页面；
但它的**设计令牌、圆角/描边/阴影的数值、缓动曲线、滚动渐隐与分段控件的交互语义**是纯 CSS/JS 数值，可以整段移植。
本次把其中 6 类落到 `admin_html.py`，React 组件的「行为」按算法重写，不引入任何构建链。

## 1. 仓库里到底有什么（按可移植性重排）

| 路径 | 内容 | 对 /admin 的可用性 |
|---|---|---|
| `code-examples/cds-theme.css` | 全套 CSS 变量 + 暗色态重载 + `@property` 注册 | ★★★ 直接抄数值（本次主体） |
| `code-examples/ClaudeMotionPresets.ts` | 弹簧参数、关键帧 | ★★★ 转成 CSS `cubic-bezier` / `transition` |
| `code-examples/SegmentedControl.tsx` | 999px 轨道 + 滑块 | ★★☆ 只抄视觉，行为用 CSS 实现 |
| `code-examples/ScrollFadeContainer.tsx` | 7 档 alpha 遮罩 + scroll-driven animation | ★★☆ 抄语义即可（见 §4 的坑） |
| `code-examples/ChatComposer.tsx` / `RichComposerWithMentions.tsx` | IME 守卫、`/` `@` 触发 | ★☆☆ 本页没有输入式对话，暂无场景 |
| `MediaLightboxViewer` / `StreamingDiffViewer` / `McpToolDecisionCard` / `CoworkAgentCanvas` | 重交互业务组件 | ✗ 依赖 React 运行时，不适用 |
| `docs/Claude Design System (CDS).md` | 手册正文（实际有第八篇，README 只提七篇） | ★★☆ 查「为什么这么定」 |

> README 说的「30 个组件可直接复用」需要 React 构建链；本次只取**可脱离框架的部分**，这点必须说清楚。

## 2. 取到的真实令牌（不是 README 的概述值）

暗色态（`[data-mode="dark"], .dark`）实测取值，已映射进 `admin_html.py` 的 `:root`：

| 类别 | CDS 原值 | 本项目令牌 |
|---|---|---|
| 品牌主色 | `--cds-clay: #d97757` / hover `#e08264` | `--accent` / `--accent-hi` |
| 表面阶梯 | surface-0 `#0b0b0b` → 1 `#151515` → 2 `#1a1a19` → 3 `#20201f` → popover `#2c2c2a` | `--bg-page` / `--bg-rail` / `--bg-surface` / `--bg-control` / `--gray-750` |
| 发丝边 | `--cds-border: hsla(0,0%,100%,.09)`、strong `.18` | `--hairline` / `--hairline-strong` |
| 文字 | primary `#f5f5f5`、secondary `#a3a3a3`、muted `#666` | 前三项照抄，muted 提亮到 `#8F8D86`（4.7:1，`#666` 在深卡上只有 3.1:1） |
| 圆角 | panel `10px`、composer `20px`、pill `999px`、气泡 `calc(8px+4px)` | `--radius-card:10` / `--radius-control:8` / `--radius-pill:999` |
| 分段控件 | track `rgba(255,255,255,.08)`、thumb `#2c2c2a` | `--seg-track` / `--seg-thumb`（thumb 提亮到 `#453730`，见 §4） |
| 阴影 | `--cds-shadow-sm/md/lg`、`--cds-shadow-popover`（双环） | `--shadow-sm/md/lg/pop` |
| 焦点 | `inset ring + 0 0 0 1.5px clay + 6px 暖光` | `--glow` |
| 缓动 | `swift .16,1,.3,1` / `expand .19,1,.22,1` / `enter .22,1,.36,1` / `snap .32,.72,0,1` / `pop .34,1.56,.64,1` / `pulse .4,0,.2,1` | `--ease-*` |
| 时间常数 | 触觉 70ms、几何咬合 120ms、面板 300ms | `--dur-tap/snap/panel` |

## 3. 本次落地清单（7 项，全部有验证）

1. **令牌层换血**：蓝 `#3D6FF0` → 陶土 `#D97757`；表面从蓝灰 `#161A21` 系换成 CDS 中性暖灰阶梯；环境光从蓝紫两团改成极淡陶土暖雾。
2. **修掉一个真 bug**：`.knob` 与 `.field` 原来**共用** `--bg-inset`，输入框/下拉框在行内没有边界、看着像纯文字。现在 `.field` 独立到 `--bg-field: #201F1E`（比行底亮一档）+ 上沿内高光，框一眼可见。
3. **分段控件改 CDS segmented**：原来是「一排各自独立的按钮」，选中项跟「当前」角标长一样，分不清是控件还是标记。现在是一条 999px 轨道 + 深色滑块 + 陶土内环，轨道 2px padding + 滑块 25px = 总高 31px，与 `.field`/`.btn` 严格对齐。
4. **发丝环替代实色边框**：卡片/行/控件全部改成 alpha 白描边（.05–.18），hover 只提亮环、不位移，整屏不抖；阴影走 CDS 三级阶梯 + popover 双环（吐司用）。
5. **CDS 动效常数**：所有过渡换成 `--ease-out` / `--ease-snap`；按钮按压 `scale(.98)` 70ms（CDS 的触觉反馈）；开关滑块 `--ease-pop`；折叠三角 `--ease-expand`。
6. **滚动渐隐（ScrollFade 语义版）**：`.chatlog` / `.wlmembers` 只在「还能往那边滚」的一侧压一层 18px 渐隐，滚到头自动化掉。状态 `bot / top / both` 由脚本写在 `data-st` 上，事件在 document 捕获阶段统一收（`scroll` 不冒泡），列表被 `render()` 重建也不用重新绑定。
7. **状态与提示**：在线徽章加陶土呼吸灯（只有「活着」的状态才呼吸）；吐司改 CDS popover 双环 + 左侧状态竖轨，`--ease-enter` 入场。

另外修了一个**分组登记遗漏**：`群聊记录` 卡代码注释写「放群务分区最前」，但 `CAT_GROUPS.grp` 没登记这个分组名，`catOf()` 兜底把它送进了「系统」分区。已登记，现在群务 6 张卡、系统 3 张卡。

## 4. 两个必须记下的坑

**① CDS 的滚动遮罩不能照抄。** 原版用 `mask-image` + `animation-timeline: scroll()`，但那会把滚动条一起裁掉（`.chatlog` 是矮滚动框，滚动条没了就等于失去「还能滚」的线索）。本项目改成 sticky 伪元素：`position:sticky` 钉在上下沿 + `pointer-events:none` + `margin:-18px` 抵消占位，遮罩不吃事件、不动滚动条。

**② CDS 的滑块色在深色卡片上不够用。** `--cds-segmented-thumb: #2c2c2a` 是配 CDS 自己的面板底色设计的；套到本项目 `#1a1a19` 卡面上，滑块与轨道对比只有 **1.4:1**，扫不出「选中哪个」。提亮到 `#453730` + 陶土内环 + 亮字 `#FFDCCD`，对比升到 ~1.9:1 并带明确色相偏移，既不抢眼又能一眼扫到。

## 5. 验证证据（不是「看起来没问题」）

工具都在 `state/_ocui/`，可重跑：

| 项 | 命令 | 结果 |
|---|---|---|
| 逐分区截图 + fit 自检 | `node shot.js ./out 1920 1080` | 8 个分区全部出图，`errs: []` |
| 6 视口 × 8 分区零溢出 | `node verify.js` | 48 组全 `ok`：横向 0 溢出、每区都有卡、0 JS 报错 |
| 关键计算样式 | `node probe.js` | accent `#D97757`、field `#201F1E` vs knob `#121211`、card `#1A1A19`、page `#0B0B0B`、switch-on `#D97757`、每段只有一个 `aria-pressed=true` |
| 滚动渐隐状态机 | 合成 60 行 `.chatlog` | 顶部 `bot`(底渐隐 opacity 1) → 底部 `top`(底渐隐 0 / 顶渐隐 1) → 中段 `both` |
| 分段控件裁切 | 1920/1366/420 三档 | 11 个 seg 全部 `scrollWidth == clientWidth`，无被挡选项 |
| 项目自带离线校验 | `python tools/_verify_admin_offline.py` | 21 张卡、`hasSeeCard: true`、无 pageerror |

尺寸变化（同一份真配置，1920×1080，改版前 → 第一轮换皮肤 → 第二轮结构简化）：

| 分区 | 改版前 | 第一轮 | 第二轮 | 说明 |
|---|---|---|---|---|
| 生图 | 1368 | 1260 | **679** | 拆出「素材」后只剩 现画(475)+工作流库(108) |
| 素材（新） | — | — | **469** | 图库(187) + 工作流选择(186) |
| 对话 | 1080 | 1080 | **965** | 卡片整体变瘦 |
| 其余 | 1080 | 1080 | ≤645 | 全部一屏装得下 |

## 5b. 第二轮：真·排布简化（2026-10-17 17:10）

第一轮只换了视觉（配色/圆角/描边），排布没动 —— 主人当场指出。第二轮做的是结构：

1. **拆「素材」分区**：图库(14 项) + 工作流选择(6 项) 从生图移出。生图 4 张卡 1260px 压不进 1080，拆完 679px。
2. **去盒子化（影响最大的一刀）**：原来 131 个设置行**每一行都是一个带底带框的格子**，扫读时全是方框抢注意力，真正的控件反而淹在里面。现在设置行默认无底无框，只靠列对齐 + 留白排成表，hover/focus 才浮起一块底；控件本身（输入框/开关/滑块）依然是实心的 —— 所以「哪里能改」比以前更清楚，不是更糊。
3. **`?` 说明钮默认隐形**：原来 opacity `.35`，131 个小圆圈连成一片麻点；现在 hover/聚焦/已展开才出现，触屏给常驻淡影。
4. **范围 chip 去框**：`[0-3600]` 那 40 多个小方框改成纯淡字。
5. **画风三档磁贴：三行竖排 → 横排换行**（名字 · 内核 · 口语），每块从 58px 降到 ~30px。
6. **分区小标题瘦身**：11px/1.58 → 10.5px/1.3，4 段省 ~40px。
7. **卡片头 30 → 26px**、卡片内边距 13/15 → 11/14。
8. **只读行不再单独加底**：整页已无盒，单独给只读项一块底反而像「坏了」，改由「只读」角标 + 灰字表达。

单卡高度：现画 590→475、图库 210→187、工作流选择 203→186、大脑 234→211、人格 363→340。
**1366×768** 从「生图 +289 / 对话 +156」变成「只剩对话 +146」（7/8 分区一屏装完）。

同步改了守护测试 `tests/_thttp.py`：分区数从 7 改 8，并新增三条断言（图库/工作流选择 必须已从 draw 归属表移出、`lib` 归属档存在）。

## 6. 遗留与待决策（不改代码，等主人点头）

- **1366×768 下「对话」还差 146px**（914 vs 768）。1920 下 8 个分区全部装得下。
  差的高度几乎全来自「人格」卡的 7 行人设正文（340px，且是能 stretch 的文本框）。
  三个选项，都还没动：
  1. 「人格」单独开一区（改 1 行 `CATS` + 1 行 `CAT_GROUPS`，对话立刻掉到 ~600px）；
  2. 人设文本框默认 7 行 → 5 行（内容不变，只是少露两行、内部可滚）；
  3. 接受窄屏滚一下（1024×768 以下本来就是滚动布局）。
- React 专属组件（Lightbox / Diffing / MCP 决策卡 / 多智能体看板）不适用，本项目没有对应场景。
- 未做：把「工作流库」磁贴并进「现画」卡（省一张卡头 ~48px）。语义上合适（都是选档位），
  但会动 `groups.splice` 的插入顺序（成员白名单定位依赖它的位置），收益不值风险。

## 7. 回滚

- 改版前整份备份：`_archive/admin_html.py.bak-20261017-cds`
- 样式源文件：`state/_ocui/new_style.css`，改完跑 `python state/_ocui/_splice_css.py` 重新拼进 `admin_html.py`（只替换 `<style>` 块，不碰 body/script）
- 生效方式：`admin_html.py` 是启动时 import 的，**需要重启 bot**（主人双击 `start_all.bat`）；不想重启可先 `python state/_ocui/serve_preview.py` 开 8090 预览真配置。
