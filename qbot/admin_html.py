# -*- coding: utf-8 -*-
"""/admin 控制台页面模板（从 bot.py 迁出，2026-09-18 模块化）。

单文件、零外部依赖。bot.py 通过 `from admin_html import ADMIN_HTML` 引用；
只有 admin_page() 用它做 .replace("__TOKEN__", token)。

版式纪律（2026-09-30 重做视觉 / 2026-10-17 换 CDS 陶土设计体系）：
  * **令牌唯一来源就是本文件顶部的 :root。** 视觉基线取自 open-claude-ui
    （Claude Design System）的**真实**暗色令牌：陶土主色 #D97757、surface-0..3
    灰阶、发丝环边框（alpha 白 .09/.18）、圆角矩阵 10/8/6/999、CDS ease-* 曲线。
    design.md（Dark Console）已不在仓库里，注释里残留的 `design.md §5.x` 只是历史
    标记、别当文档。新增任何色值/尺寸先在 :root 立令牌，别在规则里临时发明。
  * JS 侧对 DOM 的契约（id、class、aria 属性、data-cat/data-name）一个字都不能改：
    卡片、磁贴、开关、分段按钮全都是 render()/row()/control() 现场生成的。
  * 栅格列数不再由 JS 猜媒体查询 —— colsNow() 直接读 getComputedStyle 的
    --cols，所以改 CSS 断点不用同步改 JS（但**必须**同时改 .grid 的 --cols）。
  * 验版式别靠肉眼：state/_ocui/ 下有 shot.js（逐分区截图 + fit 自检）、
    verify.js（6 视口 × 7 分区零溢出/零报错）、probe.js（关键计算样式）、
    serve_preview.py（不重启 bot 也能拿真配置预览新版）。改完必跑 verify.js。
"""
ADMIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>qq-bot 控制台</title>
<!-- 内联图标：不写的话浏览器会去要 /favicon.ico，控制台里多一条 404（2026-10-26） -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23D97757'/%3E%3Ctext x='16' y='22' font-size='16' text-anchor='middle' fill='%23fff' font-family='sans-serif'%3Eq%3C/text%3E%3C/svg%3E">
<style>
/* ── 令牌 ───────────────────────────────────────────────────────────────
   视觉基线改自 open-claude-ui（Claude Design System / CDS）的**真实**暗色令牌，
   不是 README 概述里的近似值：
     · 表面阶梯 surface-0..3 / popover  = CDS 暗色 gray-900/850/830/800/750
     · 主色 clay #D97757，hover #E08264（替换原来的蓝 #3D6FF0）
     · 边框一律走「发丝环」alpha 白（.09 / .18），不再用带蓝相的实色 #242932
     · 圆角矩阵：面板 10 / 控件 8 / 徽章 6 / 胶囊 999
     · 动效常数：触觉 70ms、几何咬合 120ms、面板 300ms，曲线取 CDS ease-*
   改法纪律：**只换值与形状，不动 DOM 契约**（id/class/aria/data-cat 一个字没改）。
   新增任何色值先在本块立令牌，别在规则里临时发明。 */
:root{
  /* CDS 灰阶（暗色只用到深端，浅端留给以后做亮色主题） */
  --gray-900:#0B0B0B;--gray-880:#0F0F0F;--gray-850:#151515;--gray-830:#1A1A19;
  --gray-810:#1E1E1D;--gray-800:#20201F;--gray-750:#2C2C2A;--gray-700:#383835;
  /* CDS 陶土色阶（品牌色只有这一条线） */
  --clay:#D97757;--clay-hi:#E08264;--clay-deep:#C6613F;
  --clay-soft:rgba(217,119,87,.13);--clay-line:rgba(217,119,87,.34);--clay-ink:#F0A98D;
  /* 表面：页面 → 侧栏 → 卡片 → 控件 → 内嵌。**相邻两级必须能看出差**（见下 --bg-inset） */
  --bg-page:var(--gray-900);--bg-rail:#131312;--bg-surface:var(--gray-830);
  --bg-control:var(--gray-800);--bg-inset:#121211;--bg-field:#201F1E;
  --bg-hover:#232322;--bg-active:#1C1C1B;
  /* 发丝环（CDS 的 ring 模型：靠 alpha 白描边浮起层次，不靠实色边框） */
  --hairline:rgba(255,255,255,.09);--hairline-soft:rgba(255,255,255,.06);
  --hairline-strong:rgba(255,255,255,.18);
  --border-card:var(--hairline);--border-control:var(--hairline);--border-input:rgba(255,255,255,.10);
  --divider:rgba(255,255,255,.06);
  /* 主色 */
  --accent:var(--clay);--accent-hi:var(--clay-hi);--accent-pressed:var(--clay-deep);
  --on-accent:#FFFFFF;--text-on-accent:#F7D8CB;
  --accent-soft:var(--clay-soft);--accent-line:var(--clay-line);
  /* CDS focus-shadow：内环 + 1.5px 陶土实线 + 6px 暖光 */
  --focus-ring:var(--clay);
  --glow:inset 0 0 0 1px rgba(255,255,255,.06),0 0 0 1.5px var(--clay),0 0 8px 2px rgba(217,119,87,.30);
  /* 文字：primary 15.6:1 / secondary 7.4:1 / muted 4.7:1（都在 AA 以上） */
  --text-primary:#F5F5F5;--text-secondary:#A3A3A3;--text-muted:#8F8D86;
  --text-disabled:#5E5C58;--text-meta:#C9C7C2;
  /* 状态（跟着陶土调暖，纯饱和绿/红会跟主色打架） */
  --success:#56A878;--success-ink:#9AD9B8;--success-soft:rgba(86,168,120,.13);
  --warn:#D9A441;--warn-ink:#F0C98A;--warn-soft:rgba(217,164,65,.10);--warn-line:rgba(217,164,65,.32);
  --danger:#E05C4E;--danger-ink:#FFA89B;--danger-soft:rgba(224,92,78,.11);
  /* 分段控件（CDS segmented：浅色轨道 + 深色滑块） */
  --seg-track:rgba(255,255,255,.06);--seg-thumb:#453730;
  /* 阴影阶梯（CDS dark 值） */
  --shadow-sm:0 1px 2px rgba(0,0,0,.40),0 2px 8px rgba(0,0,0,.40);
  --shadow-md:0 2px 4px rgba(0,0,0,.40),0 6px 16px rgba(0,0,0,.40);
  --shadow-lg:0 4px 8px rgba(0,0,0,.50),0 16px 36px -2px rgba(0,0,0,.40);
  --shadow-pop:inset 0 0 0 1px rgba(255,255,255,.10),0 8px 28px rgba(0,0,0,.65),0 2px 8px rgba(0,0,0,.45);
  /* 形状 + 间距梯度 */
  --radius-card:10px;--radius-control:8px;--radius-badge:6px;--radius-pill:999px;
  --sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-5:20px;--sp-6:24px;--sp-7:32px;
  /* 动效：CDS 时间常数 + 曲线 */
  --dur-tap:70ms;--dur-snap:120ms;--dur-panel:300ms;
  --ease-swift:cubic-bezier(.16,1,.3,1);
  --ease-expand:cubic-bezier(.19,1,.22,1);
  --ease-enter:cubic-bezier(.22,1,.36,1);
  --ease-out:cubic-bezier(.165,.84,.44,1);
  --ease-pop:cubic-bezier(.34,1.56,.64,1);
  --ease-overshoot:cubic-bezier(.34,1.3,.64,1);
  --ease-pulse:cubic-bezier(.4,0,.2,1);
  --font-sans:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  --font-mono:"JetBrains Mono","Cascadia Mono",ui-monospace,Consolas,monospace;
  color-scheme:dark;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg-page);color:var(--text-primary);font:400 14px/1.58 var(--font-sans);
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
::selection{background:rgba(217,119,87,.32);color:#fff}
/* 全站滚动条：CDS 的 alpha 白，细而不断 */
.chatlog::-webkit-scrollbar,.wlmembers::-webkit-scrollbar,.catnav::-webkit-scrollbar{width:10px;height:10px}
.chatlog::-webkit-scrollbar-thumb,.wlmembers::-webkit-scrollbar-thumb,.catnav::-webkit-scrollbar-thumb{
  background:rgba(255,255,255,.13);border:3px solid transparent;background-clip:content-box;border-radius:8px}
.chatlog::-webkit-scrollbar-thumb:hover,.wlmembers::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.24);background-clip:content-box}
.chatlog,.wlmembers{scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.14) transparent}

/* ── 骨架：左侧分区栏 + 右侧主区 ───────────────────────────────────── */
.app{position:relative;display:grid;grid-template-columns:216px minmax(0,1fr);min-height:100vh}
/* 环境光：只留一团极淡的陶土暖雾（CDS 暗色几乎不用彩色光，多了就脏） */
.app::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background:radial-gradient(880px 420px at 8% -10%,rgba(217,119,87,.11),transparent 70%),
             radial-gradient(700px 320px at 96% -4%,rgba(198,97,63,.06),transparent 72%)}
.sidebar{position:sticky;top:0;z-index:1;height:100vh;display:flex;flex-direction:column;min-height:0;
  padding-top:var(--sp-1);
  background:linear-gradient(180deg,#161615,var(--bg-rail) 62%);
  border-right:1px solid var(--divider)}
.brand{display:flex;align-items:center;gap:11px;padding:18px 20px 14px}
.brand__mark{width:34px;height:34px;flex:none;display:flex;align-items:center;justify-content:center;
  border-radius:10px;background:linear-gradient(150deg,var(--clay-hi),var(--clay-deep));color:#fff;
  font:700 16px/1 var(--font-sans);
  box-shadow:0 4px 14px rgba(217,119,87,.30),inset 0 1px 0 rgba(255,255,255,.26)}
.brand__name{display:block;font-size:15px;font-weight:650;letter-spacing:.01em}
.brand__sub{display:block;margin-top:2px;font-size:11.5px;color:var(--text-muted);letter-spacing:.08em}
.navhead{padding:10px 20px 6px;font-size:11px;font-weight:600;letter-spacing:.12em;color:var(--text-muted)}
.sidebar__foot{margin-top:auto;padding:14px 20px 18px;border-top:1px solid var(--divider);
  font-size:12px;line-height:1.6;color:var(--text-muted)}
.sidebar__foot p{margin:0 0 6px}
.sidebar__foot p:last-child{margin:0}

.main{position:relative;z-index:1;min-width:0;display:flex;flex-direction:column}
.topbar{position:sticky;top:0;z-index:30;display:flex;align-items:flex-start;
  justify-content:space-between;gap:var(--sp-5);
  padding:12px 26px 11px;
  background:rgba(11,11,11,.72);
  -webkit-backdrop-filter:blur(14px) saturate(150%);backdrop-filter:blur(14px) saturate(150%);
  border-bottom:1px solid var(--divider)}
.topbar__title{margin:0;font-size:17px;font-weight:600;line-height:1.3;letter-spacing:-.005em}
.topbar__sub{margin:3px 0 0;font-size:12.5px;line-height:1.5;color:var(--text-secondary);max-width:70ch}
.topbar__right{display:flex;align-items:center;gap:10px;flex:none}
/* ── 顶部搜索（2026-11-01 总控重构，抄 AstrBot「搜索定位配置项」）──────────
   旋钮全平铺时找一项靠翻分区太慢；搜索态跨分区只留命中行（CSS 一条规则实现：
   不命中的藏、命中的不动，恢复零风险）。 */
.searchbar{display:flex;align-items:center;gap:8px;flex:1;max-width:360px;min-width:160px;margin-top:2px}
.searchbar .field{width:100%}
.searchbar__st{font-size:12px;color:var(--text-muted);white-space:nowrap}
body.searching .knob:not(.qhit){display:none}
/* ── 分区磁贴墙（2026-11-01 总控重构）：总控页上一域一磁贴，────────────────
   域总开关直接可切、一句话状态、点域名跳分区。磁贴是 CDS 的低配 card：
   发丝边框 + inset 底，关着的域整块压暗，一眼分清死活。 */
.ztiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:var(--sp-2)}
.ztile{display:flex;flex-direction:column;gap:5px;padding:12px 14px;min-width:0;
  background:var(--bg-inset);border:1px solid var(--hairline);border-radius:var(--radius-card);
  transition:opacity 160ms var(--ease-out),border-color 160ms var(--ease-out)}
.ztile--off{opacity:.55}
.ztile__top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.ztile__name{background:none;border:0;padding:0;cursor:pointer;min-width:0;
  font:inherit;font-weight:700;font-size:15px;color:var(--text-primary);letter-spacing:-.005em}
.ztile__name:hover{color:var(--clay)}
.ztile__sw{display:inline-flex;align-items:center;gap:6px;flex:none}
.ztile__stat{font-size:12px;line-height:1.5;color:var(--text-muted);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ztile__meta{font-size:11px;color:var(--text-muted);opacity:.75}
.content{width:100%;max-width:1800px;padding:14px 26px 16px}   /* 别给主区设窄上限，白白逼出换行和高度 */
.avatar{width:32px;height:32px;border-radius:var(--radius-pill);
  background:linear-gradient(150deg,var(--clay-hi),#A9553A);
  display:flex;align-items:center;justify-content:center;
  font-size:13.5px;font-weight:700;color:#fff;
  box-shadow:0 3px 10px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.24)}

/* 页头总开关：文字「开/关」必须肉眼可读，不靠滑块位置表达 */
.mastersw{display:inline-flex;align-items:center;gap:9px;flex:none;
  padding:5px 12px 5px 14px;background:var(--bg-control);
  border:1px solid var(--hairline);border-radius:var(--radius-pill)}
.mastersw__lab{font:600 13.5px/1 var(--font-sans);color:var(--text-primary)}
.mastersw__st{font:500 12.5px/1 var(--font-sans);color:var(--text-muted)}
.mastersw__st--on{color:var(--clay-ink)}

/* 提示条 = 右下角吐司（CDS popover：双环 + 大阴影），左侧一条状态竖轨 */
#msg{position:fixed;right:26px;bottom:26px;z-index:60;display:none;max-width:min(440px,calc(100vw - 52px));
  padding:12px 16px 12px 15px;border-radius:10px;background:var(--gray-750);
  -webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);
  border:1px solid var(--hairline);box-shadow:var(--shadow-pop);
  font-size:13.5px;line-height:1.5;color:var(--text-primary);
  animation:toastIn 240ms var(--ease-enter) both}
#msg::before{content:"";position:absolute;left:0;top:9px;bottom:9px;width:3px;border-radius:0 3px 3px 0;
  background:var(--text-muted)}
@keyframes toastIn{from{opacity:0;transform:translateY(12px) scale(.98)}
  to{opacity:1;transform:none}}
#msg.ok{display:block;border-color:rgba(86,168,120,.34)}
#msg.ok::before{background:var(--success)}
#msg.err{display:block;border-color:rgba(224,92,78,.34)}
#msg.err::before{background:var(--danger)}

/* ── 分区导航：一排大分类，点哪个只看那一类。**「全部」那一档没有**（主人明确否掉） */
.catnav{display:flex;flex-direction:column;gap:2px;padding:0 12px var(--sp-3);
  overflow:auto;min-height:0;flex:1}
.catbtn{position:relative;display:flex;align-items:center;gap:10px;width:100%;min-height:36px;
  padding:0 12px;cursor:pointer;background:transparent;border:1px solid transparent;
  border-radius:var(--radius-control);color:var(--text-secondary);
  font:500 14px/1 var(--font-sans);text-align:left;
  transition:background-color var(--dur-snap) var(--ease-out),color var(--dur-snap) var(--ease-out),
             border-color var(--dur-snap) var(--ease-out)}
.catbtn::before{content:"";position:absolute;left:-9px;top:9px;bottom:9px;width:3px;
  border-radius:0 3px 3px 0;background:transparent;
  transition:background-color var(--dur-snap) var(--ease-out),box-shadow var(--dur-snap) var(--ease-out)}
.catbtn:hover{background:rgba(255,255,255,.05);color:var(--text-primary)}
.catbtn[aria-selected=true]{color:#fff;
  background:linear-gradient(90deg,rgba(217,119,87,.18),rgba(217,119,87,.03));
  border-color:rgba(217,119,87,.24)}
.catbtn[aria-selected=true]::before{background:linear-gradient(180deg,#F3B79F,var(--clay));
  box-shadow:0 0 10px rgba(217,119,87,.70)}
.catbtn__n{margin-left:auto;font:400 12px/1 var(--font-mono);font-variant-numeric:tabular-nums;
  color:var(--text-muted)}
.catbtn[aria-selected=true] .catbtn__n{color:rgba(255,255,255,.72)}
.catbtn__off{display:inline-flex;align-items:center;height:19px;padding:0 8px;flex:none;
  background:rgba(0,0,0,.26);border:1px solid var(--hairline);border-radius:var(--radius-pill);
  font:500 12px/1 var(--font-sans);color:var(--text-muted)}
.catbtn[aria-selected=true] .catbtn__off{background:rgba(0,0,0,.30);border-color:rgba(255,255,255,.28);
  color:rgba(255,255,255,.88)}

/* ── 栅格与卡片 ──────────────────────────────────────────────────────
   --cols 是**列数的唯一真相**：CSS 在这里声明，JS 的 colsNow() 只读它。
   列数只由媒体查询决定，卡片宽度只由 JS 写的 --span 决定。
   别在这里写 .card[data-span]{grid-column:…} —— 属性选择器权重更高，会把窄屏规则顶掉。
   卡面取 CDS panel：发丝环当边框 + 一级阴影，hover 只把环提亮（不位移，避免整屏抖）。 */
.grid{display:grid;--cols:3;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:14px 16px;align-items:start}
.card{min-width:0;padding:11px 14px;background:var(--bg-surface);
  border:1px solid var(--border-card);border-radius:var(--radius-card);
  grid-column:span var(--span,1);
  box-shadow:0 1px 2px rgba(0,0,0,.34);
  transition:border-color 160ms var(--ease-out),box-shadow 160ms var(--ease-out)}
.card:hover{border-color:var(--hairline-strong);
  box-shadow:var(--shadow-md)}
.card--wide{--span:3}   /* 概览/预设档：默认跨满一行，窄屏由 JS 改小 */
.card__head{display:flex;align-items:center;gap:8px;min-height:26px;
  margin:-11px -14px 7px;padding:0 14px;border-bottom:1px solid var(--divider);
  background:linear-gradient(180deg,rgba(255,255,255,.032),rgba(255,255,255,0))}
/* 序号徽章：陶土软底 + 暖字，一卡一个便于扫读 */
.badge{flex:none;min-width:24px;height:18px;padding:0 6px;display:inline-flex;
  align-items:center;justify-content:center;
  border:1px solid rgba(217,119,87,.30);border-radius:var(--radius-badge);
  background:var(--clay-soft);color:var(--clay-ink);
  font:600 11px/1 var(--font-mono);font-variant-numeric:tabular-nums}
.card__title{margin:0;font-size:14.5px;font-weight:600;letter-spacing:.01em;color:var(--text-primary)}
.card__meta{margin-left:auto;font-size:12px;line-height:1.4;color:var(--text-muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* 概览：kv 网格 → 数据瓦片 */
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(200px,100%),1fr));gap:8px}
.kv__i{min-width:0;padding:9px 12px;background:var(--bg-inset);
  border:1px solid var(--hairline-soft);border-radius:var(--radius-control);
  transition:border-color 160ms var(--ease-out),background-color 160ms var(--ease-out)}
.kv__i:hover{border-color:var(--clay-line);background:#171716}
.kv__k{font-size:11.5px;line-height:1.4;color:var(--text-muted)}
.kv__v{margin-top:4px;font-size:14px;font-weight:600;line-height:1.4;color:var(--text-primary);
  overflow-wrap:anywhere}
.kv__v--mono{font-family:var(--font-mono);font-size:13px;font-weight:500;
  font-variant-numeric:tabular-nums}
.kv__v--ok{color:var(--success-ink)}
.kv__v--off{color:var(--text-secondary);font-weight:500}

/* ── 设置行 ─────────────────────────────────────────────────────────
   每项是内嵌底 + 发丝描边的一块，hover/聚焦才点亮陶土，扫读时成组不散。 */
/* min 300px：宽卡内宽 ~1622px，5×300+4×14=1556 放得下 5 列，6×300+5×14=1870 放不下。
   改列数下限前先算这个，别拍脑袋。 */
.rows{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));
  gap:3px 14px;align-items:start}
/* grid/flex item 默认 min-width:auto，会被内容里最宽的那块（如下拉里最长的 option）
   的 min-content 顶破容器 —— 窄屏上整页横向溢出。所有直接子项一律允许收缩。 */
.rows>*{min-width:0}
/* ── 去盒子化（2026-10-17 第二轮）─────────────────────────────────────
   131 个设置行原来人人一个带底带框的格子 —— 那是整页最大的视觉噪音，扫读时全是
   方框在抢注意力，真正的控件（开关/输入）反而淹在里面。
   现在设置行默认「无底无框」，只靠列对齐 + 留白排成表；hover/focus 才浮起一块底，
   控件本身（框/开关/滑块）依然是实心的，所以「哪里能改」比以前更清楚，不是更糊。
   卡片外层仍有发丝边框，整页不会散。 */
.knob{min-width:0;padding:6px 10px;background:transparent;border:1px solid transparent;
  border-radius:var(--radius-control);
  transition:background-color 140ms var(--ease-out),border-color 140ms var(--ease-out),
             box-shadow 140ms var(--ease-out)}
.knob:hover{background:rgba(255,255,255,.045)}
.knob:focus-within{background:rgba(217,119,87,.07);border-color:rgba(217,119,87,.42);
  box-shadow:0 0 0 2px rgba(217,119,87,.10)}
.knob--full{grid-column:1/-1}
/* 只读项不再单独加底：整页已经无盒，单独给只读项一块底反而显得像「坏了」；
   它有「只读」角标 + 灰字，够了。 */
.knob--ro{background:transparent}
.knob__top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px 10px}
.knob__lab{display:flex;flex-wrap:wrap;align-items:center;gap:7px;min-width:0}
.knob__name{font-size:13.5px;font-weight:500;line-height:1.3;color:var(--text-primary);
  overflow-wrap:anywhere}
.knob--ro .knob__name{color:var(--text-secondary)}
.knob__ctl{display:flex;align-items:center;gap:8px;flex:none}
.knob__desc{margin-top:5px;font-size:12.5px;line-height:1.5;color:var(--text-secondary);
  overflow-wrap:anywhere}
.knob__desc[hidden]{display:none}
/* 取值范围：纯淡字，不再套一层框（131 行里 40 多个框，纯粹是噪音） */
.knob__rng{font-family:var(--font-mono);font-size:10.5px;font-variant-numeric:tabular-nums;
  color:#7D7B75;padding:0 0 0 1px;line-height:1.4;white-space:nowrap}
/* 说明弹片：默认**完全隐形**（原来是 opacity .35，131 个小圆圈连成一片麻点），
   hover / 聚焦 / 已展开才出现。触屏没有 hover，给个常驻的淡影。 */
.knob__q{flex:none;width:16px;height:16px;padding:0;cursor:pointer;border-radius:var(--radius-pill);
  background:transparent;border:1px solid var(--hairline);color:var(--text-muted);
  font:600 10.5px/1 var(--font-sans);opacity:0;
  transition:opacity var(--dur-snap) var(--ease-out),color var(--dur-snap) var(--ease-out),
             border-color var(--dur-snap) var(--ease-out),background-color var(--dur-snap) var(--ease-out)}
.knob:hover .knob__q,.knob:focus-within .knob__q{opacity:.85}
.knob__q:hover{background:var(--clay-soft);border-color:var(--clay-line);color:var(--clay-ink)}
.knob__q[aria-expanded=true]{opacity:1;background:var(--clay-soft);border-color:var(--clay-line);
  color:var(--clay-ink)}
body.descon .knob__q{display:none}
@media (hover:none){.knob__q{opacity:1}}

/* 折叠块：长说明（档位提示 / 文本槽 / 能力清单）默认只占一行标题 */
.rows>.fold{grid-column:1/-1;background:var(--bg-inset);
  border:1px solid var(--hairline-soft);border-radius:var(--radius-control);
  transition:border-color var(--dur-snap) var(--ease-out)}
.rows>.fold:hover{border-color:rgba(217,119,87,.28)}
.fold__sum{position:relative;list-style:none;cursor:pointer;padding:7px 12px 7px 28px;
  font-size:12.5px;font-weight:500;line-height:1.4;color:var(--text-secondary);
  border-radius:var(--radius-control);user-select:none;
  transition:color var(--dur-snap) var(--ease-out),background-color var(--dur-snap) var(--ease-out)}
.fold__sum::-webkit-details-marker{display:none}
.fold__sum::before{content:"";position:absolute;left:12px;top:50%;width:5px;height:5px;
  border-right:1.5px solid var(--text-muted);border-bottom:1.5px solid var(--text-muted);
  margin-top:-4px;transform:rotate(-45deg);
  transition:transform 180ms var(--ease-expand),border-color var(--dur-snap) var(--ease-out)}
.fold[open]>.fold__sum::before{margin-top:-2px;transform:rotate(45deg);border-color:var(--clay-ink)}
.fold__sum:hover{color:var(--text-primary);background:rgba(255,255,255,.04)}
.fold[open]>.fold__sum{color:var(--text-primary);
  border-bottom:1px solid var(--hairline-soft);border-radius:var(--radius-control) var(--radius-control) 0 0}
.fold__text{padding:9px 12px;font-size:12.5px;line-height:1.6;color:var(--text-secondary);
  overflow-wrap:anywhere}
.fold__box{padding:9px 10px}
.ro{font-size:13.5px;font-weight:500;line-height:1.3;color:var(--text-secondary);
  font-family:var(--font-mono);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.cst{font-size:13px;font-weight:500;line-height:1.2;color:var(--text-secondary)}
.cst--bad{color:var(--danger,#c0392b);font-size:12px;margin-left:2px;border:1px solid var(--danger,#c0392b);
  border-radius:6px;padding:1px 5px;white-space:nowrap}

/* 分组小标题（现画卡四段）：从「占一整行的小标题」压成「极窄的列头」，
   4 段省下 ~40px，还让四组像一张表的分节而不是四块独立面板。 */
.sect{display:flex;align-items:center;gap:10px;margin:5px 0 1px;grid-column:1/-1;
  font-size:10.5px;font-weight:600;letter-spacing:.12em;line-height:1.3;color:var(--text-muted)}
.sect::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--divider),transparent)}
/* .rows 里的「块」——磁贴组 / 折叠块 / 名单 / 聊天流 / 空态——必须通栏。 */
.rows>.tiles,.rows>.slots,.rows>.fold,.rows>.chatwrap,.rows>.wlist,
.rows>.empty{grid-column:1/-1}

/* ── 控件 ─────────────────────────────────────────────────────────
   控件统一 31px 高；真正省高度的是别让它们换行。 */
/* 开关：CDS 暗色里选中的是实心陶土 + 白钮（原来是蓝色渐变 + 双层光晕） */
.switch{position:relative;flex:none;width:38px;height:22px;padding:0;cursor:pointer;
  background:var(--seg-track);border:1px solid var(--hairline);
  border-radius:var(--radius-pill);box-shadow:inset 0 1px 3px rgba(0,0,0,.42);
  transition:background-color 200ms var(--ease-out),border-color 200ms var(--ease-out),
             box-shadow 200ms var(--ease-out)}
.switch__dot{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:var(--radius-pill);
  background:#8A8880;box-shadow:0 1px 3px rgba(0,0,0,.55);
  transition:transform 220ms var(--ease-pop),background-color 200ms var(--ease-out)}
.switch:hover{border-color:var(--hairline-strong)}
.switch[aria-checked=true]{background:var(--clay);border-color:transparent;
  box-shadow:0 0 0 3px rgba(217,119,87,.16),inset 0 1px 0 rgba(255,255,255,.18)}
.switch[aria-checked=true] .switch__dot{transform:translateX(16px);background:#fff}
.switch:active .switch__dot{width:19px}
.switch[aria-checked=true]:active .switch__dot{transform:translateX(13px)}
/* 分段控件：CDS segmented —— 浅色轨道 + 深色滑块（原来是一排各自独立的按钮，
   选中项跟旁边的「当前」标签长得一样，分不出这是控件还是标记）。
   轨道上下各 2px padding，滑块 25px ⇒ 总高 31px，跟别的控件对齐。 */
.seg{display:inline-flex;flex-wrap:nowrap;justify-content:flex-end;gap:2px;
  padding:2px;max-width:100%;overflow-x:auto;scrollbar-width:none;
  background:var(--seg-track);border:1px solid rgba(255,255,255,.05);border-radius:var(--radius-pill)}
.seg::-webkit-scrollbar{height:0}
.seg__b{height:25px;padding:0 12px;flex:none;cursor:pointer;background:transparent;border:none;
  border-radius:var(--radius-pill);color:var(--text-secondary);
  font:500 12.5px/1 var(--font-sans);white-space:nowrap;
  transition:background-color var(--dur-snap) var(--ease-out),color var(--dur-snap) var(--ease-out),
             box-shadow var(--dur-snap) var(--ease-out),transform var(--dur-tap) var(--ease-out)}
.seg__b:hover{background:rgba(255,255,255,.07);color:var(--text-primary)}
.seg__b[aria-pressed=true]{background:var(--seg-thumb);color:#FFDCCD;font-weight:600;
  box-shadow:0 1px 3px rgba(0,0,0,.55),inset 0 0 0 1px rgba(217,119,87,.55)}
.seg__b:active{transform:scale(.97)}
/* 当前值不在预选项里（如「活跃=自定义」/取值为空）时补的那一个：虚环提示会被覆盖 */
.seg__b--cur{cursor:default;background:transparent;color:var(--text-muted);
  box-shadow:inset 0 0 0 1px var(--clay-line);opacity:.9}
/* 输入框：**必须比所在的 .knob 亮一档**，否则在 #121211 的行底上直接隐形
   （改版前 --bg-inset 同时给了 .knob 和 .field，截图里输入框像纯文字）。 */
.field{height:31px;padding:0 11px;background:var(--bg-field);
  border:1px solid var(--border-input);border-radius:var(--radius-control);
  box-shadow:0 1px 2px rgba(0,0,0,.30),inset 0 1px 0 rgba(255,255,255,.04);
  color:var(--text-primary);font:400 13px/1 var(--font-sans);
  transition:border-color var(--dur-snap) var(--ease-out),box-shadow var(--dur-snap) var(--ease-out),
             background-color var(--dur-snap) var(--ease-out)}
.field::placeholder{color:var(--text-disabled)}
.field:hover{border-color:var(--hairline-strong)}
input.num{width:92px}
input.txt{width:100%;max-width:230px}
.selwrap{position:relative;display:inline-flex;align-items:center;max-width:100%}
.selwrap::after{content:"";position:absolute;right:12px;width:6px;height:6px;pointer-events:none;
  border-right:1.5px solid var(--text-secondary);border-bottom:1.5px solid var(--text-secondary);
  transform:rotate(45deg) translate(-1px,-3px)}
select.field{appearance:none;-webkit-appearance:none;padding-right:30px;max-width:230px}
select.field option{background:var(--bg-field);color:var(--text-primary)}
textarea.field.txtarea{width:100%;height:auto;min-height:56px;max-height:44vh;
  padding:8px 11px;resize:vertical;line-height:1.55;
  font:400 12.5px/1.55 var(--font-sans);overflow-wrap:anywhere}
/* 2026-10-19：加 max-height —— 人设正文那种几千字的编辑器，行数一多会把整个「对话」
   分区顶出一屏（实测 16 行时 chat 超出 523px）。留 44vh 上限 + 仍可手动拖高。 */
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,
summary:focus-visible{outline:none;border-color:rgba(217,119,87,.70);box-shadow:var(--glow)}
/* 按钮：CDS 二级 = alpha 填充；主按钮 = 实心陶土；按压一律 scale(.98) 70ms 触觉反馈 */
.btn{height:31px;padding:0 15px;cursor:pointer;background:rgba(255,255,255,.07);
  border:1px solid var(--hairline);border-radius:var(--radius-control);
  color:var(--text-primary);font:500 12.5px/1 var(--font-sans);
  transition:background-color var(--dur-snap) var(--ease-out),border-color var(--dur-snap) var(--ease-out),
             color var(--dur-snap) var(--ease-out),box-shadow var(--dur-snap) var(--ease-out),
             transform var(--dur-tap) var(--ease-out)}
.btn:hover{background:rgba(255,255,255,.11);border-color:var(--hairline-strong)}
.btn:active{transform:scale(.98)}
.btn--primary{background:var(--clay);border-color:transparent;color:var(--on-accent);font-weight:600;
  box-shadow:0 2px 10px rgba(217,119,87,.28),inset 0 1px 0 rgba(255,255,255,.18)}
.btn--primary:hover{background:var(--clay-hi);border-color:transparent;
  box-shadow:0 6px 20px rgba(217,119,87,.34),inset 0 1px 0 rgba(255,255,255,.22)}

/* 磁贴（工作流库 / 预设档） */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));gap:8px}
.tile{display:block;width:100%;padding:9px 12px;text-align:left;cursor:pointer;
  background:var(--bg-inset);border:1px solid var(--hairline-soft);
  border-radius:var(--radius-control);color:inherit;font-family:var(--font-sans);
  transition:background-color var(--dur-snap) var(--ease-out),border-color var(--dur-snap) var(--ease-out),
             box-shadow var(--dur-snap) var(--ease-out),transform var(--dur-snap) var(--ease-out)}
.tile:hover{background:#171716;border-color:var(--clay-line);transform:translateY(-1px);
  box-shadow:var(--shadow-sm)}
.tile__top{display:flex;align-items:center;gap:8px}
.tile__name{font-size:13px;font-weight:600;color:var(--text-primary);overflow-wrap:anywhere}
.tile__kv{display:block;margin-top:5px;font-size:12px;line-height:1.45;color:var(--text-secondary);
  overflow-wrap:anywhere}

/* 画风三档：三条业务线 = 动画人物/写实人物/厚涂人物，内核名只做次级小字；
   选中 = 陶土软底 + 陶土环（原来是实心蓝块，三行长文字压在饱和色上很糊）。
   排布改成「名字 + 内核 + 口语」横排换行：原来强制三行竖排，三块磁贴白占 58px。 */
.st3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;grid-column:1/-1}
.st3__b{display:flex;flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:2px 9px;
  padding:8px 12px;cursor:pointer;background:var(--bg-inset);border:1px solid var(--hairline-soft);
  border-radius:var(--radius-control);color:var(--text-primary);font-family:var(--font-sans);
  text-align:left;min-width:0;
  transition:background-color 160ms var(--ease-out),border-color 160ms var(--ease-out),
             box-shadow 160ms var(--ease-out),transform 160ms var(--ease-out)}
.st3__b:hover{background:#171716;border-color:var(--clay-line);transform:translateY(-1px);
  box-shadow:var(--shadow-sm)}
.st3__b[aria-pressed=true]{color:#fff;border-color:rgba(217,119,87,.55);
  background:linear-gradient(165deg,rgba(217,119,87,.22),rgba(217,119,87,.08));
  box-shadow:0 6px 22px rgba(217,119,87,.16),inset 0 1px 0 rgba(255,255,255,.12)}
.st3__name{flex:none;font:600 14px/1.25 var(--font-sans);overflow-wrap:anywhere}
.st3__sub{flex:none;font:500 11px/1.25 var(--font-mono);opacity:.68}
.st3__b[aria-pressed=true] .st3__sub{color:var(--clay-ink);opacity:.95}
.st3__desc{flex:none;font:400 11.5px/1.3 var(--font-sans);opacity:.78}

/* 文本槽（工作流文本格编辑器） */
.slots{display:grid;gap:10px;min-width:0}
.slot{min-width:0}
.slot__top{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.slot__name{font-size:12.5px;font-weight:500;line-height:1.25;color:var(--text-primary);
  overflow-wrap:anywhere}
.slot__hint{margin:0 0 5px;font-size:12px;line-height:1.45;color:var(--text-muted);
  overflow-wrap:anywhere}

/* 名单 / 聊天记录行 */
.tag{display:inline-flex;align-items:center;height:20px;padding:0 9px;flex:none;
  border:1px solid var(--hairline);border-radius:var(--radius-pill);
  background:rgba(255,255,255,.05);font-size:11.5px;font-weight:500;line-height:1;color:var(--text-secondary)}
.tag--on{background:var(--clay-soft);border-color:rgba(217,119,87,.42);color:var(--clay-ink)}
/* 连接徽章：陶土呼吸灯（CDS StatusIndicators 的做法——只有「活着的」状态才呼吸） */
#conn.tag--on{background:rgba(86,168,120,.14);border-color:rgba(86,168,120,.40);color:var(--success-ink);
  gap:6px}
#conn.tag--on::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--success);
  animation:connBreath 2.4s var(--ease-pulse) infinite}
@keyframes connBreath{0%,100%{box-shadow:0 0 0 0 rgba(86,168,120,.50)}
  60%{box-shadow:0 0 0 5px rgba(86,168,120,0)}}
.wlrow{display:flex;align-items:center;gap:10px;min-height:30px;padding:3px 10px;
  border-radius:var(--radius-control);transition:background-color 140ms var(--ease-out)}
.wlrow:hover{background:rgba(255,255,255,.05)}
.wlrow+.wlrow{border-top:1px solid var(--divider)}
.wlrow__name{font-size:13.5px;font-weight:500;color:var(--text-primary);overflow-wrap:anywhere}
.wlrow__qq{font-family:var(--font-mono);font-size:12px;color:var(--text-muted);
  font-variant-numeric:tabular-nums}
.wlrow .btn{margin-left:auto;flex:none}
.wladd{display:flex;gap:8px;margin-top:4px;padding-top:9px;border-top:1px solid var(--divider);
  min-width:0}   /* select 里的 option 很长，不给 0 会被 min-content 顶破容器 */
.wladd .field{flex:1;min-width:0;width:auto}
.wladd select.field{max-width:none}   /* 群名下拉要放得下整行「○ 群名（群号，N 人）」，别被截断 */
.wlhead{margin-top:8px;padding-top:8px;border-top:1px solid var(--divider);
  font-size:11.5px;letter-spacing:.04em;color:var(--text-muted)}
/* 群成员候选名单是「名单」不是正文：给固定高度 + 卡内滚动，绝不把整页撑长 */
.wlmembers{max-height:88px;overflow:auto;margin-top:3px;padding-right:2px}
/* 群聊记录卡：群下拉 + 消息流 */
.chatwrap{display:grid;gap:9px;min-width:0}
.chatlog{max-height:196px;overflow:auto;display:grid;gap:3px;padding:6px;
  background:var(--bg-inset);border:1px solid var(--hairline-soft);border-radius:var(--radius-control)}
.chatrow{align-items:baseline;gap:9px}
.chatrow .wlrow__qq{margin-left:auto;flex:none}
.chatrow__text{font-size:13px;line-height:1.5;color:var(--text-secondary);
  overflow-wrap:anywhere;flex:1;min-width:170px}
.chatrow--out{background:var(--clay-soft);box-shadow:inset 2px 0 0 var(--clay)}
.chatrow--out .wlrow__name{color:var(--clay-ink);font-weight:600}
.chatrow--out .chatrow__text{color:var(--text-primary)}

/* ── 滚动渐隐（CDS ScrollFade）─────────────────────────────────────
   只在「还能往下滚」的那一侧压一层渐隐，滚到头就化掉 —— 抄的是 CDS 的
   scroll-driven fade 思路，但用 sticky 伪元素实现：mask-image 会把滚动条
   一起裁掉，而 sticky 覆盖层不吃事件（pointer-events:none）。
   状态由脚本写 data-st（top/bot/both/空），这里只管显隐。 */
.chatlog::before,.chatlog::after,.wlmembers::before,.wlmembers::after{
  content:"";position:sticky;left:0;z-index:2;pointer-events:none;display:block;
  height:18px;opacity:0;transition:opacity 160ms var(--ease-out)}
.chatlog::before,.wlmembers::before{top:0;margin-bottom:-18px;
  background:linear-gradient(to bottom,var(--bg-inset) 18%,rgba(18,18,17,0))}
.chatlog::after,.wlmembers::after{bottom:0;margin-top:-18px;
  background:linear-gradient(to top,var(--bg-inset) 18%,rgba(18,18,17,0))}
.chatlog[data-st=top]::before,.chatlog[data-st=both]::before,
.wlmembers[data-st=top]::before,.wlmembers[data-st=both]::before{opacity:1}
.chatlog[data-st=bot]::after,.chatlog[data-st=both]::after,
.wlmembers[data-st=bot]::after,.wlmembers[data-st=both]::after{opacity:1}

/* 空态：占位框，别像没加载出来 */
.empty{margin:0;padding:10px 12px;font-size:12.5px;line-height:1.5;color:var(--text-secondary);
  background:var(--bg-inset);border:1px dashed var(--hairline);
  border-radius:var(--radius-control);overflow-wrap:anywhere}
/* 提示条（如生图总开关已关）横跨整行 + 左侧警示轨 */
.notice{padding:11px 13px;background:var(--warn-soft);border:1px solid var(--warn-line);
  border-left:3px solid var(--warn);border-radius:var(--radius-control);
  font-size:13px;line-height:1.5;color:var(--text-primary)}
.grid>.notice{grid-column:1/-1}
/* ── 插件（2026-10-26 第二轮：主人「格式好丑、太杂太满，简约一点」）─────────
   第一版是「一包一张卡，开关+全部参数+一大段提示词全摊开」，三张卡就把屏撑满。
   现在：列表一行一个包（名字 + 一句话 + 开关 + 配置），参数和提示词收进右侧抽屉，
   点「配置」才出现 —— 一屏能看完全部插件，改动仍然是一步。 */
.plist{grid-column:1/-1;display:flex;flex-direction:column;min-width:0}
.prow{display:flex;align-items:center;gap:12px;min-width:0;padding:9px 2px;
  border-top:1px solid var(--divider)}
.prow:first-child{border-top:0;padding-top:2px}
.prow__main{flex:1;min-width:0}
.prow__name{display:flex;align-items:center;gap:8px;font-size:13.5px;font-weight:600;
  line-height:1.3;color:var(--text-primary)}
.prow__desc{margin-top:3px;font-size:12px;line-height:1.5;color:var(--text-muted);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prow__ctl{display:flex;align-items:center;gap:10px;flex:none}
.prow--off .prow__name{color:var(--text-secondary)}
.prow--off .prow__desc{opacity:.65}

/* 抽屉：右侧滑出，Esc / 点遮罩 / 「好了」都能关。抽屉挂在 body 上，
   整页 load() 重画栅格不会把它清掉（改完参数还能接着改）。 */
.scrim{position:fixed;inset:0;z-index:70;background:rgba(0,0,0,.44);
  -webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px);
  animation:fadeIn 150ms var(--ease-out) both}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.drawer{position:fixed;top:0;right:0;bottom:0;z-index:71;width:min(560px,100vw);
  display:flex;flex-direction:column;min-height:0;background:var(--bg-surface);
  border-left:1px solid var(--hairline-strong);box-shadow:-30px 0 70px rgba(0,0,0,.55);
  animation:drawerIn 190ms var(--ease-enter) both}
@keyframes drawerIn{from{transform:translateX(28px);opacity:0}to{transform:none;opacity:1}}
.drawer__head{display:flex;align-items:center;gap:10px;padding:12px 16px;
  border-bottom:1px solid var(--divider)}
.drawer__title{flex:1;min-width:0;font-size:15px;font-weight:600;color:var(--text-primary)}
.drawer__body{flex:1;min-height:0;overflow:auto;padding:6px 12px 14px}
.drawer__body>.knob{padding:8px 6px}
/* 抽屉里说明一直展开，所以「?」弹片没意义，去掉免得点出一片空白 */
.drawer__body .knob__q{display:none}
.drawer__sec{margin:12px 6px 2px;font-size:10.5px;font-weight:600;letter-spacing:.12em;
  color:var(--text-muted)}
.drawer__sec:first-child{margin-top:4px}
.drawer__note{margin:0 6px 6px;font-size:12px;line-height:1.5;color:var(--text-muted)}
.drawer__foot{display:flex;align-items:center;gap:10px;padding:10px 16px;
  border-top:1px solid var(--divider);font-size:12px;color:var(--text-muted)}
.drawer__foot .btn{margin-left:auto}

/* 插件市场：一屏扫完的卡片格子 */
.mkwrap{grid-column:1/-1;display:flex;flex-direction:column;gap:12px;min-width:0}
.mklist{grid-column:1/-1;display:grid;gap:10px;
  grid-template-columns:repeat(auto-fill,minmax(min(250px,100%),1fr))}
.mk{display:flex;flex-direction:column;gap:7px;min-width:0;padding:11px 12px;
  background:var(--bg-inset);border:1px solid var(--hairline);border-radius:var(--radius-control)}
.mk--on{border-color:rgba(217,119,87,.30);background:linear-gradient(180deg,rgba(217,119,87,.07),transparent 60%),var(--bg-inset)}
.mk__top{display:flex;align-items:center;gap:8px;min-width:0}
.mk__name{font-size:13.5px;font-weight:600;color:var(--text-primary);min-width:0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mk__desc{font-size:12px;line-height:1.55;color:var(--text-secondary);
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.mk__tags{display:flex;flex-wrap:wrap;gap:6px}
.mk__foot{display:flex;align-items:center;gap:8px;margin-top:auto;padding-top:2px}
.mk__st{font-size:11.5px;line-height:1.4;color:var(--text-muted)}
.btn--sm{height:27px;padding:0 11px;font-size:12.5px}

/* AstrBot 官方市场（只读，2026-10-26）：本地市场下面一条 —— 搜索 + 同步 + 结果行。
   这里的包是 AstrBot 框架的 Python 包，装不进本机，所以只有「仓库」链接，没有安装按钮。 */
.asblock{grid-column:1/-1;display:flex;flex-direction:column;gap:9px;
  padding-top:12px;border-top:1px solid var(--divider)}
.asblock__head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.asblock__title{font-size:13px;font-weight:600;color:var(--text-primary)}
.asblock__note{font-size:11.5px;line-height:1.6;color:var(--text-muted);margin:0}
.asbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.asbar input{flex:1 1 200px;min-width:0;height:30px;padding:0 10px;font-size:12.5px;
  background:var(--bg-inset);color:var(--text-primary);
  border:1px solid var(--hairline);border-radius:var(--radius-control);outline:none}
.asbar input:focus{border-color:var(--clay-line)}
.aslist{display:grid;gap:6px;max-height:min(300px,34vh);overflow-y:auto;
  overscroll-behavior:contain}
.asrow{display:flex;gap:10px;align-items:baseline;min-width:0;padding:7px 10px;
  background:var(--bg-inset);border:1px solid var(--hairline);border-radius:var(--radius-control)}
.asrow__n{font-size:12.5px;font-weight:600;color:var(--text-primary);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:34%}
.asrow__d{font-size:11.5px;line-height:1.5;color:var(--text-secondary);min-width:0;flex:1;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.asrow__a{font-size:11px;color:var(--text-muted);white-space:nowrap}
.asrow a{font-size:11.5px;color:var(--clay-ink);text-decoration:none;white-space:nowrap}
.asrow a:hover{text-decoration:underline}
.asempty{font-size:12px;color:var(--text-muted);margin:0}
/* 筛选下拉（2026-10-26 主人：「加筛选框」）+ 加载更多 —— 以前 limit 写死 24，
   页面上连「还有多少」都不说，看着像市场里只有 24 个插件。 */
.asbar select{height:30px;padding:0 8px;font-size:12.5px;max-width:190px;
  background:var(--bg-inset);color:var(--text-primary);
  border:1px solid var(--hairline);border-radius:var(--radius-control);outline:none}
.asbar select:focus{border-color:var(--clay-line)}
.asbar__n{font-size:11.5px;color:var(--text-muted);white-space:nowrap}
.asmore{display:flex;align-items:center;gap:10px;padding-top:2px}
.asmore .btn{flex:0 0 auto}

/* 抽屉里的「来源」：图库站点 / 索引 JSON 直接可点，省得翻代码找 */
.plinks{display:flex;flex-wrap:wrap;gap:6px 12px;margin:0 6px 8px}
.plinks a{font-size:12px;color:var(--clay-ink);text-decoration:none;
  border-bottom:1px dashed rgba(217,119,87,.45)}
.plinks a:hover{border-bottom-style:solid}

/* 情绪标准返图（2026-10-26）：情绪 chips + 缩略图网格，点一下钉住 / 再点取消 */
.moodbar{display:flex;flex-wrap:wrap;gap:6px;margin:0 6px 8px}
.moodchip{height:26px;padding:0 10px;font-size:12px;cursor:pointer;
  background:var(--bg-inset);color:var(--text-secondary);
  border:1px solid var(--hairline);border-radius:999px;outline:none}
.moodchip:hover{border-color:var(--clay-line);color:var(--text-primary)}
.moodchip--on{background:rgba(217,119,87,.14);border-color:rgba(217,119,87,.42);
  color:var(--clay-ink);font-weight:600}
.mgrid{display:grid;gap:8px;margin:0 6px;
  grid-template-columns:repeat(auto-fill,minmax(76px,1fr))}
.mcell{position:relative;display:flex;flex-direction:column;gap:4px;min-width:0;
  padding:5px;cursor:pointer;background:var(--bg-inset);
  border:1px solid var(--hairline);border-radius:var(--radius-control)}
.mcell:hover{border-color:var(--clay-line)}
.mcell--on{border-color:rgba(217,119,87,.62);background:rgba(217,119,87,.10);
  box-shadow:0 0 0 1px rgba(217,119,87,.35)}
.mcell img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:6px;
  background:rgba(0,0,0,.18);display:block}
.mcell__n{font-size:10.5px;line-height:1.3;color:var(--text-muted);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mcell__on{position:absolute;top:8px;right:8px;min-width:18px;height:18px;line-height:18px;
  text-align:center;font-size:11px;font-weight:700;color:#fff;background:var(--clay);
  border-radius:999px;padding:0 5px;box-shadow:0 1px 4px rgba(0,0,0,.45)}
.moodtip{font-size:11.5px;color:var(--text-muted);margin:0 6px 8px}
/* 素材市场的进度提示：钉在抽屉顶部。不然格子多的时候人是滚在下面的，
   「正在搬 13/120」「搬好了」全在视野外，看着就像点了没反应。 */
.mktip{position:sticky;top:0;z-index:3;margin:0 0 8px;padding:5px 8px;
  background:var(--bg-surface);border-radius:6px}
/* 素材市场（2026-10-27）：格子要能塞下「搬过来」按钮和原因文字，所以比情绪格子宽一号 */
.mgrid--mk{grid-template-columns:repeat(auto-fill,minmax(min(122px,100%),1fr))}
.mgrid--mk .mcell{padding:6px}
.mgrid--mk .mcell .btn{width:100%;justify-content:center;margin-top:1px}
.mgrid--mk .mcell__n{white-space:normal;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
/* 缩略图还没下到（镜像单张 5~10 秒）时先给个安静的空位，别让浏览器画破图 */
.mcell__ph{width:100%;aspect-ratio:1/1;border-radius:6px;display:flex;
  align-items:center;justify-content:center;font-size:11px;
  background:var(--bg-inset);color:var(--text-muted)}
/* 点图放大：只有一张大图 + 名字，不翻帧。点任意处 / 按 Esc 关掉。
   注意这里**不能**给 img 加 loading 或切 src —— 一动 src 就会黑一下再闪出来。 */
.zoom{position:fixed;left:50%;top:50%;z-index:72;transform:translate(-50%,-50%);
  display:flex;flex-direction:column;align-items:center;gap:8px;
  max-width:min(78vw,560px);max-height:86vh;padding:10px;
  background:var(--bg-surface);border:1px solid var(--hairline-strong);
  border-radius:var(--radius-card, 14px);box-shadow:0 24px 60px rgba(0,0,0,.55);
  cursor:zoom-out;animation:zoomIn 150ms var(--ease-enter) both}
@keyframes zoomIn{from{transform:translate(-50%,-50%) scale(.94);opacity:0}
  to{transform:translate(-50%,-50%) scale(1);opacity:1}}
.zoom img{display:block;max-width:100%;max-height:74vh;width:auto;height:auto;
  border-radius:8px;background:var(--bg-inset)}
.zoom__n{font-size:12px;color:var(--text-secondary)}

[hidden]{display:none!important}

/* ── 断点 ──────────────────────────────────────────────────────────
   每条都要同步改 .grid 的 --cols（JS 靠它排 span），别只改 grid-template-columns。 */
@media (max-width:1180px){.grid{--cols:2;grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:820px){.grid{--cols:1;grid-template-columns:minmax(0,1fr)}}
@media (max-width:980px){
  .app{grid-template-columns:minmax(0,1fr)}
  .sidebar{position:static;height:auto;border-right:none;border-bottom:1px solid var(--divider)}
  .brand{padding:16px 20px 12px}
  .navhead{display:none}
  .catnav{flex-direction:row;flex-wrap:nowrap;gap:6px;padding:0 20px 14px;
    overflow-x:auto;overflow-y:hidden;flex:none}
  .catnav::-webkit-scrollbar{height:0}
  .catbtn{width:auto;flex:none;min-height:34px;padding:0 14px}
  .catbtn::before{display:none}
  .catbtn__n{margin-left:2px}
  .sidebar__foot{display:none}
  .topbar{padding:16px 20px 13px}
  .content{padding:18px 20px 40px}
  .st3{grid-template-columns:minmax(0,1fr)}
}
@media (max-width:560px){
  .topbar{flex-direction:column;align-items:stretch;gap:12px}
  .topbar__right{justify-content:space-between}
  .content{padding:16px 14px 36px}
  .catnav{padding:0 14px 12px}
  .brand{padding:14px 14px 10px}
  #msg{right:14px;left:14px;bottom:14px;max-width:none}
}
@media (prefers-reduced-motion:reduce){
  *{transition-duration:.01ms!important;animation-duration:.01ms!important}
}
/* ── 矮屏档（笔记本 1366×768 / 窗口没最大化）─────────────────────────
   硬要求是**每个分区一屏装完、不用往下拉**。
   改任何尺寸前都跑一遍 fit 检查（state/_ocui/shot.js，逐分区量 scrollHeight vs 视口高）。 */
@media (max-height:900px){
  .content{padding:10px 22px 12px}
  .topbar{padding:9px 22px 8px}
  .topbar__title{font-size:16px}
  .topbar__sub{font-size:12px;margin-top:2px}
  .grid{gap:11px 14px}
  .card{padding:10px 13px}
  .card__head{min-height:27px;margin:-10px -13px 8px;padding:0 13px}
  .rows{gap:5px 12px}
  .knob{padding:5px 10px}
  .knob__name{font-size:13px}
  .knob__desc{margin-top:4px;font-size:12px}
  .sect{margin:0}
  .field,.btn{height:29px}
  .seg{padding:2px}
  .seg__b{height:23px;padding:0 11px}
  .switch{width:34px;height:20px}
  .switch__dot{width:14px;height:14px;top:2px;left:2px}
  .switch[aria-checked=true] .switch__dot{transform:translateX(14px)}
  .st3__b{padding:8px 12px}
  .st3__name{font-size:13.5px}
  .kv__i{padding:7px 11px}
  .tile{padding:7px 11px}
  .wlrow{min-height:28px;padding:2px 10px}
  .wlhead{margin-top:6px;padding-top:6px}
  .wlmembers{max-height:78px}
  .chatlog{max-height:160px}
  .fold__sum{padding:6px 12px 6px 28px}
  .empty{padding:8px 11px}
}
/* 更矮的屏（1366×768 笔记本 / 分屏）：页头那句说明先让位，控件整体再收一档 */
@media (max-height:800px){
  .content{padding:8px 18px 10px}
  .topbar{padding:7px 18px 6px}
  .topbar__title{font-size:15px}
  .topbar__sub{display:none}
  .grid{gap:9px 12px}
  .card{padding:9px 11px}
  .card__head{min-height:24px;margin:-9px -11px 7px;padding:0 11px}
  .rows{gap:4px 12px}
  .knob{padding:4px 9px}
  .knob__name{font-size:12.5px}
  .sect{margin:0}
  .field,.btn{height:27px;font-size:12px}
  .seg__b{height:21px;padding:0 10px;font-size:12px}
  input.num{width:78px}
  input.txt,select.field{max-width:180px}
  .switch{width:31px;height:18px}
  .switch__dot{width:12px;height:12px;top:2px;left:2px}
  .switch[aria-checked=true] .switch__dot{transform:translateX(13px)}
  .st3__b{padding:6px 10px;gap:2px}
  .st3__desc{display:none}
  .kv__i{padding:6px 10px}
  .kv__v{font-size:13px}
  .tile{padding:6px 10px}
  .wlrow{min-height:26px;padding:2px 9px}
  .wlmembers{max-height:60px}
  .chatlog{max-height:132px}
  .fold__sum{padding:5px 10px 5px 26px}
  .empty{padding:7px 10px}
  .notice{padding:9px 11px}
}

/* ── 模型选择窗（2026-10-29）：自定义API 模型旁的「拉取」按钮弹这个。
      点按钮才现拉 /v1/models，平时界面零改动 —— 653 个模型只住在这里，带搜索。 */
.mpick{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:72;
  width:min(420px,calc(100vw - 40px));max-height:min(520px,calc(100vh - 80px));
  display:flex;flex-direction:column;background:var(--bg-card);
  border:1px solid var(--border-card);border-radius:var(--radius-card);
  box-shadow:0 18px 50px rgba(0,0,0,.5);animation:drawerIn 160ms var(--ease-enter) both}
.mpick__head{display:flex;align-items:center;gap:10px;padding:11px 14px;
  border-bottom:1px solid var(--hairline-soft)}
.mpick__title{flex:1;min-width:0;font-size:14px;font-weight:600;color:var(--text-primary)}
.mpick__body{flex:1;min-height:0;display:flex;flex-direction:column;padding:10px 14px 12px}
.mpick__search{margin-bottom:8px;flex:none}
.mpick__list{flex:1;min-height:120px;overflow:auto;border:1px solid var(--hairline-soft);
  border-radius:var(--radius-control);background:var(--bg-inset)}
.mpick__item{display:block;width:100%;text-align:left;padding:7px 12px;cursor:pointer;
  border:0;border-bottom:1px solid var(--hairline-soft);background:none;
  color:var(--text-primary);font:12.5px/1.4 var(--font-sans,monospace);
  overflow-wrap:anywhere}
.mpick__item:hover{background:rgba(255,255,255,.06)}
.mpick__item:last-child{border-bottom:0}
.mpick__empty{padding:18px 12px;text-align:center;font-size:12.5px;color:var(--text-muted)}
.mpick__count{font-size:11.5px;color:var(--text-muted);margin-top:7px}
/* ── 模型测速窗（2026-10-29 深夜）：跟模型选择窗同一套壳（.mpick.probe），
      内里是几行 key/value + 一句判定。首字/总耗时分开列，别让「想得慢」冒充「模型慢」。 */
.probe{width:min(460px,calc(100vw - 40px));max-height:min(560px,calc(100vh - 80px))}
.probe__out{overflow:auto;border:1px solid var(--hairline-soft);
  border-radius:var(--radius-control);background:var(--bg-inset);padding:4px 0}
.probe__wait{padding:18px 12px;text-align:center;font-size:12.5px;color:var(--text-muted)}
.probe__row{display:flex;gap:10px;padding:6px 12px;font-size:12.5px;
  border-bottom:1px solid var(--hairline-soft);align-items:baseline}
.probe__row:last-child{border-bottom:0}
.probe__k{flex:none;width:74px;color:var(--text-muted)}
.probe__v{flex:1;min-width:0;color:var(--text-primary);overflow-wrap:anywhere;
  font:12.5px/1.45 var(--font-sans,monospace)}
.probe__v.good{color:#5fbf7f}
.probe__v.warn{color:#d9a441}
.probe__v.bad{color:#d76b6b}
.probe__verdict{margin-top:9px;padding:9px 12px;border-radius:var(--radius-control);
  font-size:12.5px;line-height:1.5;overflow-wrap:anywhere;
  border:1px solid var(--hairline-soft);color:var(--text-primary)}
.probe__verdict.ok{background:rgba(95,191,127,.10);border-color:rgba(95,191,127,.34)}
.probe__verdict.bad{background:rgba(215,107,107,.10);border-color:rgba(215,107,107,.34)}
.probe__bad{padding:14px 12px;font-size:12.5px;color:#d76b6b;overflow-wrap:anywhere}
.probe__advice{margin-top:7px;font-size:11.5px;line-height:1.5;color:var(--text-muted)}
</style></head><body><div class="app">

<aside class="sidebar">
  <div class="brand">
    <span class="brand__mark" aria-hidden="true">Q</span>
    <span>
      <b class="brand__name">qq-bot</b>
      <span class="brand__sub">控制台</span>
    </span>
  </div>
  <div class="navhead">分区</div>
  <nav class="catnav" id="catnav" aria-label="分区"></nav>
  <div class="sidebar__foot">
    <p>密钥只存在本机浏览器，不外传；密钥类字段只回显配置状态，不回明文。</p>
    <p>状态每 20 秒自动刷新。</p>
  </div>
</aside>

<main class="main">
<header class="topbar">
  <div>
    <h1 class="topbar__title">qq-bot 控制台</h1>
    <p class="topbar__sub">改完立即生效；标「重启」的要重启 bot 才生效。密钥类只回显配置状态，不回明文。</p>
  </div>
  <div class="searchbar">
    <input class="field" type="search" id="searchbox" placeholder="搜配置：名字或 key，如「冷却」"
           aria-label="搜索配置项" autocomplete="off">
    <span class="searchbar__st" id="searchst"></span>
  </div>
  <div class="topbar__right">
    <div class="mastersw" id="descbox">
      <span class="mastersw__lab">说明</span>
      <button class="switch" id="dsw" type="button" role="switch" aria-checked="false" aria-label="显示全部说明"><span class="switch__dot"></span></button>
      <span class="mastersw__st" id="dswst">关</span>
    </div>
    <div class="mastersw" id="mastersw" hidden>
      <span class="mastersw__lab">生图</span>
      <button class="switch" id="msw" type="button" role="switch" aria-checked="false" aria-label="生图总开关"><span class="switch__dot"></span></button>
      <span class="mastersw__st" id="mswst">关</span>
    </div>
    <span class="tag" id="conn">连接中…</span>
    <span class="avatar" id="avatar" aria-hidden="true"></span>
  </div>
</header>

<div class="content">
<div id="msg" role="status"></div>
<section class="card" id="tokbox" hidden>
  <div class="card__head"><span class="badge">00</span><h2 class="card__title">桥接密钥</h2></div>
  <div class="knob">
    <div class="knob__top">
      <div class="knob__lab"><span class="knob__name">bridge_token</span></div>
      <div class="knob__ctl"><input id="tok" class="field txt" type="text" placeholder="粘贴 bridge_token"></div>
    </div>
    <div class="knob__desc">在 config.json 顶层，或看 state/bridge-token.json。只存在本机浏览器里，不会外传。</div>
  </div>
  <div style="margin-top:12px"><button class="btn btn--primary" onclick="saveTok()">保存并连接</button></div>
</section>
<div class="grid" id="grid">
  <div class="notice" id="drawoff" data-cat="draw" hidden>生图总开关已关闭 —— 下面这些配置还能改，但不会真的去画图。</div>
  <section class="card card--wide" id="ovcard" data-cat="sys">
    <div class="card__head"><span class="badge">01</span><h2 class="card__title">概览</h2><span class="card__meta" id="ovwhen"></span></div>
    <div class="kv" id="ov"></div>
  </section>
  <section class="card card--wide" id="pfcard" data-cat="sys">
    <div class="card__head"><span class="badge">02</span><h2 class="card__title">预设档</h2><span class="card__meta">一档写一组，点一下整组应用</span></div>
    <div id="pf"></div>
  </section>
</div>
</div>
</main>
</div>
<script>
var TOKEN = "__TOKEN__";
var LAST = null;
/* 分区导航（2026-09-15 主人定）：侧栏一排分区按钮，点哪个只显示那一类，一屏看一个模块。
   **「全部」那一档不要** —— 全部内容堆一起会长到翻不完，主人明确否掉；默认停在第一个分类。
   切换只切卡片的 hidden、不重建 DOM（保住别处没保存的输入），切完按「可见卡片」重算 span。 */
var DRAWOFF = false;
/* 分区表（2026-11-01 总控重构）：现在由 bot.py 的 ZONES 下发（/api/config 的 d.zones），
   前端只在 render() 时照单重建 —— 以后加减分区只改 bot.py，这里不再维护映射。
   前端只留两条兜底：后端没下发的分组名照旧落「系统」（catOf），
   后端没发 zones（旧版 bot + 新页面的短暂窗口）就退回这份硬编码，页面不塌。 */
var CATS = [{key:"总控",name:"总控"},{key:"对话",name:"对话"},{key:"群友",name:"群友"},
             {key:"生图",name:"生图"},{key:"素材",name:"素材"},{key:"反推",name:"反推"},
             {key:"视频",name:"视频"},{key:"插件",name:"插件"},{key:"系统",name:"系统"}];
var CATKEYS = ["总控","对话","群友","生图","素材","反推","视频","插件","系统"];
var ZONEMAP = {   /* group 名 → 分区（与 bot.py ZONES 一致；只在 zones 缺席时兜底用） */
  "大脑":"对话","人格管理":"对话","人格":"对话","识图":"对话","语音":"对话","私聊":"对话",
  "群友":"群友","群聊能力":"群友","群聊记录":"群友","成员白名单":"群友","黑话":"群友","表达":"群友","时机":"群友","群务":"群友",
  "现画":"生图","工作流库":"生图",
  "图库":"素材","工作流选择":"素材",
  "反推":"反推",
  "视频":"视频","视频生成":"视频",
  "插件列表":"插件","技能":"插件","插件市场":"插件",
  "概览":"总控","预设档":"总控","会话总控":"总控",
  "管理":"系统","只读":"系统","系统":"系统"};
function buildZones(zones){
  /* 用后端下发的分区表重建 CATS / ZONEMAP（render() 每次都跑，几十个键，开销可忽略） */
  if(!zones||!zones.length)return;
  var cs=[],ks=[],map={};
  zones.forEach(function(z){
    if(ks.indexOf(z.zone)<0){cs.push({key:z.zone,name:z.zone});ks.push(z.zone);}
    (z.groups||[]).forEach(function(g){map[g]=z.zone;});});
  if(ks.indexOf("系统")<0){cs.push({key:"系统",name:"系统"});ks.push("系统");}
  /* 前端自产的两张卡不在后端 ZONES 里：磁贴墙（组名「分区磁贴」）永远住总控页。
     会话总控由后端登记；这张没登记的就近归总控，别掉进「系统」。 */
  map["分区磁贴"]=ks[0];
  CATS=cs;CATKEYS=ks;ZONEMAP=map;}
function catOf(g){
  /* 分区归属：分组名 → 分区（bot.py ZONES 下发；漏登记的兜底进「系统」）。
     2026-10-26 起插件不再是「一包一个组」，统一成「插件列表」一组，所以这里不用再按前缀认。 */
  return ZONEMAP[g]||"系统";}
 var CURCAT = "总控";      /* 默认分类 = 总控（进门第一眼：状态 + 预设档 + 磁贴墙） */
 var SEARCHQ = "";        /* 顶部搜索框当前词；非空 = 搜索态（跨分区只显示命中行） */
var CARDS = [];           /* [{el:卡片元素, cat:归属分类, n:条目数, ms:min_span, notice:是否提示条}] */
/* 说明开关（2026-09-30）：真实 desc 最长 403 字、档位提示也有 403 字，全铺开一页装不下。
   默认**收起** —— 每行只留「名字 + 控件」，说明挂在行内的 ? 弹片上，页头一个总开关。
   一屏装得下是硬要求，说明要读再点开，别默认占地方。 */
var DESCON = false;
function foldBox(title,node,open){
 var d=el("details","fold");
 if(open)d.setAttribute("open","");
 d.appendChild(el("summary","fold__sum",title));
 d.appendChild(el("div","fold__box",node));
 return d;}
function foldText(title,txt,open){
 var d=el("details","fold");
 if(open)d.setAttribute("open","");
 d.appendChild(el("summary","fold__sum",title));
 d.appendChild(el("div","fold__text",txt));
 return d;}
function setDesc(on){
 DESCON=!!on;
 try{localStorage.setItem("qqbot_desc",DESCON?"1":"0");}catch(e){}
 document.body.classList.toggle("descon",DESCON);
 var ds=document.querySelectorAll("#grid .knob__desc");
 for(var i=0;i<ds.length;i++){ds[i].hidden=!DESCON;ds[i].setAttribute("data-open",DESCON?"1":"0");}
 var qs=document.querySelectorAll("#grid .knob__q");
 for(var j=0;j<qs.length;j++)qs[j].setAttribute("aria-expanded",DESCON?"true":"false");
 var fs=document.querySelectorAll("#grid details.fold");
 for(var k=0;k<fs.length;k++)fs[k].open=DESCON;
 var b=$("dsw");
 if(b){b.setAttribute("aria-checked",DESCON?"true":"false");
   $("dswst").textContent=DESCON?"开":"关";
   $("dswst").className="mastersw__st"+(DESCON?" mastersw__st--on":"");}}
/* 画风三档显示名（design.md §5.24）：主人眼里是三条业务线，krea2/anima 是内核名，
   只许做次级小字（排障时看得见背后跑什么），永不作 UI 主标签；映射外的档回退档名本身。 */
var STY_NAME = {"krea2":"写实人物","anima":"动画人物","厚涂":"厚涂人物"};
var STY_SUB  = {"krea2":"内核 krea2","anima":"内核 Anima2.5D 恢复版","厚涂":"内核 古风二次元"};
var STY_SAY  = {"krea2":"口语「写实 / 真人 / 摄影」","anima":"口语「动漫 / 二次元 / 动画」",
                "厚涂":"口语「厚涂 / 古风」"};
function styName(v){return STY_NAME[v]||String(v==null?"":v);}
function $(id){return document.getElementById(id);}
function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;
 if(txt!=null)e.textContent=txt;return e;}
function say(t,ok){var m=$("msg");m.className=ok?"ok":"err";m.textContent=t||"";
 clearTimeout(window._t);window._t=setTimeout(function(){m.className="";},5000);}
/* ── 滚动渐隐（open-claude-ui / CDS ScrollFade 的可移植版）────────────────
   原版用 scroll-driven animation 拉 7 档 alpha 遮罩；这里保留它的**语义**
   （只在「还能往那边滚」的一侧渐隐，滚到头就化掉），实现换成 sticky 伪元素 +
   data-st 开关 —— mask-image 会把滚动条一起裁掉，伪元素则不吃事件。
   scroll 不冒泡，所以在 document 捕获阶段统一收，列表被 render() 重建也不用重新绑定。 */
function scState(n){var st=n.scrollTop,max=n.scrollHeight-n.clientHeight;
 if(max<=2)return"";var t=st>2,b=st<max-2;return t&&b?"both":(b?"bot":(t?"top":""));}
function syncSc(n){if(!n||!n.getAttribute)return;var v=scState(n);
 if((n.getAttribute("data-st")||"")!==v)n.setAttribute("data-st",v);}
function scWatch(n){if(!n)return;syncSc(n);
 setTimeout(function(){syncSc(n);},60);    /* 内容插完高度才算得准 */
 setTimeout(function(){syncSc(n);},420);}  /* 兜异步填充（成员列表是新拉的） */
document.addEventListener("scroll",function(e){var t=e.target;
 if(t&&t.classList&&(t.classList.contains("chatlog")||t.classList.contains("wlmembers")))syncSc(t);},true);
function saveTok(){TOKEN=$("tok").value.trim();
 try{localStorage.setItem("qqbot_token",TOKEN);}catch(e){} load();}
function load(){
 if(!TOKEN){try{TOKEN=localStorage.getItem("qqbot_token")||"";}catch(e){}}
 if(!TOKEN){$("tokbox").hidden=false;return;}
 fetch("/api/config",{headers:{"X-Bridge-Token":TOKEN}}).then(function(r){
   if(r.status===401){$("tokbox").hidden=false;say("密钥不对（401），重新填一次",false);throw new Error("401");}
   return r.json();}).then(function(d){
   if(!d||!d.ok){say((d&&d.error)||"读配置失败",false);return;}
   $("tokbox").hidden=true;LAST=d;render(d);status();}).catch(function(e){
   /* ⚠️ 别把这写成空块（2026-10-07 教训）：render() 里抛的错会在这里被静默吞掉，
      控制台一片干净，主人只看到「分区没了」却没有任何线索。至少要说出来。 */
   if(String(e&&e.message||e)==="401")return;
   say("页面渲染出错："+(e&&e.message||e),false);
   if(window.console&&console.error)console.error("[admin] load/render",e);});}
function kv(box,k,v,mono,state){
 var i=el("div","kv__i");i.appendChild(el("div","kv__k",k));
 var val=el("div","kv__v"+(mono?" kv__v--mono":"")+(state?" kv__v--"+state:""),v);
 i.appendChild(val);box.appendChild(i);}
function status(){
 $("ovwhen").textContent="状态每 20 秒自刷";
 fetch("/api/status",{headers:{"X-Bridge-Token":TOKEN}}).then(function(r){return r.json();})
 .then(function(s){
  if(!s||!s.ok)return;
  var c=s.comfy||{},box=$("ov");box.innerHTML="";
  kv(box,"机器人",(s.nickname||"—")+(s.self_id?"（"+s.self_id+"）":""),false);
  kv(box,"群",(s.groups||[]).join("、")||"—",true);
  kv(box,"图库",(s.images||[]).length+" 张",false);
  kv(box,"ComfyUI",(c.server_up?"在线":"离线")+(c.enabled?"":"（现画已关）"),false,c.server_up?"ok":"off");
  kv(box,"画图档",c.default_style?styName(c.default_style)
      +(STY_NAME[c.default_style]?"（"+c.default_style+"）":""):"—",true);
  kv(box,"队列",(c.drawing?"渲染中":"空闲")+"　排队 "+(c.queued||0)+"　本小时 "+(c.done_this_hour||0)+"/"+(c.max_per_hour==null?"—":c.max_per_hour),true);
  kv(box,"大脑",(s.brain||"—")+"　"+(s.role||"默认人格"),true);
  kv(box,"黑话","已确认 "+(s.slang_confirmed||[]).length+"　待审 "+(s.slang_pending||[]).length,false);
  var a=$("avatar");if(a&&!a.textContent)a.textContent=(s.nickname||"Q").slice(0,1);
  var cp=$("conn");cp.className="tag tag--on";cp.textContent="在线";})
 .catch(function(){var cp=$("conn");cp.className="tag";cp.textContent="离线";});}
function matchProfile(d,kvmap){
 var items={};(d.groups||[]).forEach(function(g){(g.items||[]).forEach(function(i){items[i.label]=i;});});
 return Object.keys(kvmap).every(function(k){
   var it=items[k];if(!it)return false;
   var want=kvmap[k];
   if(typeof want==="boolean")return it.type==="bool"?!!it.value===want:String(it.display)===String(want);
   return String(it.display)===String(want)||String(it.value)===String(want);});}
function colsNow(){
  /* 读 CSS 声明的 --cols（.grid 上的令牌），**不能读计算列数**。
     计算列数会被 JS 自己写的 --span 反过来影响：span=3 的卡在 1 列栅格里会撑出
     2 条隐式轨道，于是实测永远是 3 列 —— 死循环，窄屏永远收不回单列（实测 420px 下
     整页错位、卡片被压成 42px 宽、文字一列一字竖排）。
     改栅格断点时记得同步改 .grid 的 --cols。 */
  var g=$("grid");if(!g)return 3;
  var n=parseInt(getComputedStyle(g).getPropertyValue("--cols"),10);
  return n>0?n:3;}
function spanOf(g,cols){
 /* 一张卡的理想 span：重=3/中=2/轻=1，且不超过列数；
    min_span 是卡片自己声明的下限（比如工作流库这种磁贴卡）。 */
 var n=(g.items||[]).length;
 var d=n>=8?3:(n>=5?2:1);
 if(g.min_span&&d<g.min_span)d=Math.min(g.min_span,cols);
 return d>cols?cols:d;}
function planSpans(groups,cols){
 /* cols 格一行：贪心补位 —— 一行凑不满就把本行最后一张拉宽，保证每行正好填满、
    span 永远不超过列数（超了会撑出隐式列，把整页挤歪）。
    试过加一条「小卡别独占一行、把它推给下一行」的避孤行规则：没用。DOM 顺序固定，
    CSS Grid 只能按序摆放，把小卡推下去等于跳过它（排布里直接漏掉一张卡）。
    现在的贪心在固定顺序下已接近最优，别再加规则。 */
 var spans=[],i=0;
 while(i<groups.length){
   var start=i,used=0;
   while(i<groups.length){
     var d=spanOf(groups[i],cols);
     if(i>start&&used+d>cols)break;
     spans.push(d);used+=d;i++;}
   var slack=cols-used;
   if(slack>0)spans[i-1]+=slack;}
 return spans;}
function libTiles(lib,items){
 /* 工作流库磁贴：内置档 + 工作流目录里的文件档，点一下就把「画图档」切过去。
    数据来自 /api/config 里画图档那项的 lib 字段，页面不自己扫目录。 */
 var cur="";items.forEach(function(i){if(i.label==="画图档")cur=(i.value==null?"":String(i.value));});
 var box=el("div","tiles");
 lib.forEach(function(w){
   var b=el("button","tile");b.type="button";
   var top=el("span","tile__top");
   top.appendChild(el("span","tile__name",styName(w.name)));
   if(w.name===cur)top.appendChild(el("span","tag tag--on","当前"));
   b.appendChild(top);
   /* 档名与显示名不同时把原名留在小字里，排障/对群口令时对得上 */
   b.appendChild(el("span","tile__kv",(styName(w.name)!==w.name?"档名 "+w.name+" · ":"")
     +(w.kind==="文件"?"工作流文件 · "+w.file:"内置档 · "+w.engine+" 引擎")));
   b.onclick=function(){if(w.name===cur){say("「"+styName(w.name)+"」本来就是当前档",true);return;}
     say("正在切到「"+styName(w.name)+"」…",true);set({key:"画图档",value:w.name});};
   box.appendChild(b);});
 return box;}
function styleTiles(it){
 /* 画风三档（design.md §5.24）：三张并排大磁贴，显示名当主标签、内核名只做次级小字。
    点击发真实档名（显示名不进 config，群口令/MCP 仍用内核名）；点当前档只提示不请求。 */
 var cur=(it.value==null?"":String(it.value));
 var box=el("div","st3");
 (it.values||[]).forEach(function(v){
   var b=el("button","st3__b");b.type="button";
   b.setAttribute("aria-pressed",cur===String(v)?"true":"false");
   b.appendChild(el("span","st3__name",styName(v)));
   b.appendChild(el("span","st3__sub",STY_SUB[v]||("内核 "+v)));
   if(STY_SAY[v])b.appendChild(el("span","st3__desc",STY_SAY[v]));
   b.onclick=function(){
     if(cur===String(v)){say("「"+styName(v)+"」本来就是当前档",true);return;}
     say("正在切到「"+styName(v)+"」…",true);set({key:(it.key||it.label),value:v});};
   box.appendChild(b);});
 return box;}
function renderDraw(rows,items){
 /* 现画卡专用排布（design.md §5.24）：顶部画风三档选择器 + 当前档用法说明，
    其余开关照 KNOBS 顺序用 .sect 分成 档位/权限/出图管线/节奏与队列 四段，不重排不漏项。 */
 var SECT={"写实档实现":"档位","兜底档":"档位","工作流目录":"档位",
           "现画":"权限","群友可用":"权限",
           "查设定":"出图管线","查证后端":"出图管线","润色":"出图管线","润色嘴":"出图管线",
           "画师/角色选配":"出图管线",
           "发后删图":"节奏与队列","冷却":"节奏与队列","每小时上限":"节奏与队列","排队上限":"节奏与队列"};
 var last=null;
 items.forEach(function(it){
   if(it.label==="画图档"){
     rows.appendChild(styleTiles(it));
     var h=it.hints?it.hints[String(it.value==null?"":it.value)]:null;
     /* 档位说明最长 403 字 —— 折起来，只留一行标题 */
     if(h)rows.appendChild(foldText("这一档怎么用",h,DESCON));
     return;}
   var s=SECT[it.label];
   if(s&&s!==last){rows.appendChild(el("div","sect",s));last=s;}
   rows.appendChild(row(it));});}
function textSlots(ts){
 /* 文本槽：工作流文件里有标题的文本格（画师串这类）。改的是 config 覆盖值
    （comfyui.wf_texts），空着保存 = 还原工作流原值。走 /api/wf_text。 */
 var box=el("div","slots");
 ts.forEach(function(t){
   t.slots.forEach(function(s){
     var r=el("div","slot");
     var top=el("div","slot__top");
     var nm=t.slots.length>1||ts.length>1?t.wf+" · "+s.title:s.title;
     top.appendChild(el("span","slot__name",nm));
     if(s.overridden)top.appendChild(el("span","tag","已覆盖"));
     r.appendChild(top);
     if(s.hint)r.appendChild(el("div","slot__hint",s.hint));
     var ta=document.createElement("textarea");ta.className="field txtarea";ta.rows=2;
     ta.value=(s.value==null?"":s.value);
     ta.placeholder="清空保存 = 还原工作流原值";
     ta.onchange=function(){saveSlot(t.wf,s.title,ta.value);};
     r.appendChild(ta);
     box.appendChild(r);});});
 /* 文本格整体折起来：默认只留一行标题，要改再点开 */
 var n=0;ts.forEach(function(t){n+=t.slots.length;});
 return foldBox("文本槽 · "+n+" 处（点开改）",box,DESCON);}
function saveSlot(wf,title,value){
 say("正在保存「"+title+"」…",true);
 fetch("/api/wf_text",{method:"POST",headers:{"Content-Type":"application/json",
   "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},
   body:JSON.stringify({wf:wf,title:title,value:value})})
 .then(function(r){return r.json();}).then(function(d){
   if(d&&d.ok){say(d.message||"存好了",true);load();}
   else{say((d&&(d.message||d.error))||"没存上",false);}})
 .catch(function(e){say("请求失败："+e,false);});}
function _secage(s){
 return s<60?s+"s 前":(s<3600?Math.round(s/60)+" 分前":Math.round(s/3600)+" 时前");}
function chatApi(body,done){
 say("正在改群监控名单…",true);
 fetch("/api/group_watch",{method:"POST",headers:{"Content-Type":"application/json",
   "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify(body)})
 .then(function(r){return r.json();}).then(function(d){
   if(d&&d.ok){say(d.watching?"已加入监控":"已移出监控",true);if(done)done();}
   else{say((d&&(d.error))||"没改成",false);}})
 .catch(function(e){say("请求失败："+e,false);});}
function chatBody(){
 /* 群聊记录：无状态设计——群列表(/api/groups=bot 实际在的群)和监控名单(/health.groups)
    每次现拉；选群后 /api/history 拉最近 60 条。未监控的群给「加入监控」按钮。 */
 var wrap=el("div","chatwrap");
 var bar=el("div","wladd");
 var sel=el("select","field");sel.style.flex="1";sel.style.width="auto";
 var rf=el("button","btn","刷新");rf.type="button";
 bar.appendChild(sel);bar.appendChild(rf);
 wrap.appendChild(bar);
 var box=el("div");wrap.appendChild(box);
 var WG={};
 function load(gid){
  box.innerHTML="";
  if(!gid)return;
  if(!WG[gid]){
   box.appendChild(el("div","wlhead","该群未加入监控——bot 不记录消息，也就没有聊天可看。"));
   var row=el("div","wlrow");
   row.appendChild(el("span","wlrow__name","加入后开始记录并正常响应（立即生效，不用重启）"));
   var b=el("button","btn btn--primary","加入监控");b.type="button";
   b.onclick=function(){chatApi({group_id:gid,on:true},function(){WG[gid]=true;fill();});};
   row.appendChild(b);box.appendChild(row);
   return;}
  fetch("/api/history?group="+gid+"&limit=60",{headers:{"X-Bridge-Token":TOKEN}})
  .then(function(r){return r.json();}).then(function(d){
   if(!d||!d.ok)throw new Error((d&&d.error)||"记录拉不到");
   var lg=el("div","chatlog");
   (d.messages||[]).forEach(function(m){
     var r=el("div","wlrow chatrow"+(m.out?" chatrow--out":""));
     r.appendChild(el("span","wlrow__name",(m.out?"":"")+ (m.who||("QQ "+m.qq))));
     r.appendChild(el("span","chatrow__text",m.text||""));
     r.appendChild(el("span","wlrow__qq",
       m.ago_sec!=null?_secage(m.ago_sec):""));
     lg.appendChild(r);});
   if(!(d.messages||[]).length)
     lg.appendChild(el("p","empty","还没有记录——加入监控后的新消息才会进来（记录存内存，重启清零）"));
   scWatch(lg);
   box.appendChild(lg);})
  .catch(function(e){box.innerHTML="";
   box.appendChild(el("p","empty",e.message||"记录读取失败"));});}
 function fill(){
  fetch("/health").then(function(r){return r.json();}).then(function(h){
   WG={};(h.groups||[]).forEach(function(g){WG[String(g)]=true;});
   return fetch("/api/groups",{headers:{"X-Bridge-Token":TOKEN}}).then(function(r){return r.json();});})
  .then(function(gr){
   if(!gr||!gr.ok)throw new Error((gr&&gr.error)||"群列表拉不到");
   var gs=gr.groups||[];
   sel.innerHTML="";
   if(!gs.length){box.innerHTML="";
     box.appendChild(el("p","empty","bot 没在任何群里"));return;}
   var want=sel.value;
   gs.forEach(function(g){
     var gid=String(g.group_id);
     var o=document.createElement("option");o.value=gid;
     o.textContent=(WG[gid]?"● ":"○ ")+(g.name||gid)+"（"+gid+"，"+(g.members||"?")+"人）";
     sel.appendChild(o);});
   var has=gs.some(function(g){return String(g.group_id)===want;});
   if(want&&has)sel.value=want;
   load(sel.value);})
  .catch(function(e){box.innerHTML="";
   box.appendChild(el("p","empty",e.message||"读取失败"));});}
 sel.onchange=function(){load(sel.value);};
 rf.onclick=function(){fill();};
 fill();
 wrap.appendChild(foldText("● / ○ 是什么意思？",
   "● = 监控中（bot 记录消息并正常响应）；○ = 只在群里、不记录。想看哪个群先「加入监控」；历史存内存，重启清零。",
   DESCON));
 return wrap;}
function wlApi(body,done){
 say("正在改白名单…",true);
 fetch("/api/whitelist",{method:"POST",headers:{"Content-Type":"application/json",
   "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify(body)})
 .then(function(r){return r.json();}).then(function(d){
   if(d&&d.ok){say(d.message||"改好了",true);if(done)done();}
   else{say((d&&(d.message||d.error))||"没改成",false);}})
 .catch(function(e){say("请求失败："+e,false);});}
function wlRow(name,qq,btnText,on){
 var r=el("div","wlrow");
 r.appendChild(el("span","wlrow__name",name));
 if(qq)r.appendChild(el("span","wlrow__qq",qq));
 var b=el("button","btn",btnText);b.type="button";b.onclick=on;
 r.appendChild(b);return r;}
function wlManual(done){
 /* 成员列表拉不到时的退化路：直接填 QQ 号 */
 var box=el("div","wladd");
 var inp=document.createElement("input");inp.type="text";inp.className="field";
 inp.placeholder="成员列表没拉到，直接填 QQ 号";
 var go=el("button","btn btn--primary","加入白名单");go.type="button";
 go.onclick=function(){if(!inp.value.trim()){say("先填 QQ 号",false);return;}
   wlApi({action:"add",qq:inp.value.trim()},done);};
 box.appendChild(inp);box.appendChild(go);return box;}
function wlBody(){
 /* 成员白名单：无状态设计——名单和候选成员每次现拉，加人=点那行的「加入」，
    连续加多人就是多点几下；没有会被页面刷新重置的选中态。
    改完只重画这一张卡（fill），不 load() 整页——整页重载会把别处没保存的输入清掉。 */
 var wrap=el("div","wlist");
 var box=el("div");wrap.appendChild(box);
 function fill(){
  box.innerHTML="";
  fetch("/api/whitelist",{headers:{"X-Bridge-Token":TOKEN}}).then(function(r){return r.json();})
  .then(function(w){
   if(!w||!w.ok)throw new Error((w&&w.error)||"读白名单失败");
   var list=w.list||[];
   if(!list.length)box.appendChild(el("p","empty","白名单是空的 —— 下面点成员行里的「加入」"));
   list.forEach(function(m){
     box.appendChild(wlRow(m.name||("QQ "+m.qq),m.qq,"移除",
       function(){wlApi({action:"remove",qq:m.qq},fill);}));});
   box.appendChild(el("div","wlhead","群成员（点「加入」加进白名单，可连续加多人）"));
   var mbox=el("div","wlmembers");box.appendChild(mbox);
   fetch("/api/members",{headers:{"X-Bridge-Token":TOKEN}}).then(function(r){return r.json();})
   .then(function(mr){
     if(!mr||!mr.ok)throw new Error("members");
     var inWl={};list.forEach(function(m){inWl[String(m.qq)]=true;});
     var ms=(mr.members||[]).filter(function(m){return m.qq!=null&&!inWl[String(m.qq)];});
     if(!ms.length)mbox.appendChild(el("p","empty","群里的人都在白名单里了"));
     ms.forEach(function(m){
       mbox.appendChild(wlRow((m.name||m.qq)+"（"+m.qq+"）",null,"加入",
         function(){wlApi({action:"add",qq:String(m.qq)},fill);}));});
     scWatch(mbox);})
   .catch(function(){mbox.appendChild(wlManual(fill));scWatch(mbox);});})
  .catch(function(e){box.innerHTML="";
    box.appendChild(el("p","empty",e.message||"白名单读取失败"));});}
 fill();
 wrap.appendChild(foldText("白名单 = 群管理员？",
   "白名单成员等同群管理员：能改配置、管现画（就是 config 的 admin.owner_qq，改动立即生效）。",
   DESCON));
 return wrap;}
/* ── 插件：列表 + 抽屉（2026-10-26 第二轮）─────────────────────────────
   第一版「一包一张卡、全摊开」被主人否掉（太杂太满）。现在：
     列表一行一个包 → 名字 + 一句话 + 开关 + 「配置」；
     参数和提示词在右侧抽屉里，点「配置」才出来。
   数据全在 /api/config 的「插件列表」组：items 每项带 skill（哪个包）/ kind（toggle|param|prompt）。 */
function pluginList(g){
 var metas=g.plugins||[],items=g.items||[];
 var box=el("div","plist");
 if(!metas.length){
   box.appendChild(el("p","empty","还没装插件。去「插件市场」挑一个，或把技能包放进 skills/ 目录。"));
   return box;}
 metas.forEach(function(m){
   var its=items.filter(function(i){return i.skill===m.id;});
   var tg=its.filter(function(i){return i.kind==="toggle";})[0];
   var r=el("div","prow"+(m.on?"":" prow--off"));
   var main=el("div","prow__main");
   var nm=el("div","prow__name");
   nm.appendChild(document.createTextNode(m.name));
   if(m.native)nm.appendChild(el("span","tag tag--on","工具已接"));
   else if(m.tools&&m.tools.length)nm.appendChild(el("span","tag","工具未接"));
   else nm.appendChild(el("span","tag","纯提示词"));
   main.appendChild(nm);
   main.appendChild(el("div","prow__desc",m.desc||""));
   r.appendChild(main);
   var ctl=el("div","prow__ctl");
   if(tg)ctl.appendChild(control(tg,false));
   var b=el("button","btn btn--sm","配置");b.type="button";
   b.setAttribute("data-skill",m.id);
   b.onclick=function(){openPlugin(m,its);};
   ctl.appendChild(b);
   r.appendChild(ctl);box.appendChild(r);});
 return box;}

var DRAWER=null;
function closeDrawer(){
 if(DRAWER){
   [DRAWER.scrim,DRAWER.panel].forEach(function(n){
     if(n&&n.parentNode)n.parentNode.removeChild(n);});}
 DRAWER=null;
 document.removeEventListener("keydown",drawerEsc);}
function drawerEsc(e){if(e.key==="Escape"||e.keyCode===27)closeDrawer();}
/* 情绪标准返图（2026-10-26）：主人要「选定几张作为几个情绪的标准返图」。
   面板 = 情绪 chips + 缩略图网格。点一下钉住 / 再点取消，立刻存（POST /api/pins）。
   钉住的图会排在该情绪候选最前面，而且不受「同一会话不重发」限制（点了名就是每次都要它）。 */
function thumbUrl(u){
 if(!u)return "";
 if(u.charAt(0)!=="/")return u;        /* 远程图（梗鲸）是完整 URL，别往后面加 token */
 return u+(u.indexOf("?")>=0?"&":"?")+"token="+encodeURIComponent(TOKEN);}
function moodPanel(m){
 var wrap=el("div","");
 var bar=el("div","moodbar");
 var grid=el("div","mgrid");
 var more=el("div","asmore");
 var tip=el("p","moodtip","读图中…");
 wrap.appendChild(bar);wrap.appendChild(grid);wrap.appendChild(more);wrap.appendChild(tip);
 var PAGE=12;
 var cur={mood:"",ids:[],items:[],shown:PAGE},CELLS={};
 function mark(){
   Object.keys(CELLS).forEach(function(id){
     var c=CELLS[id],k=cur.ids.indexOf(id),on=k>=0;
     c.className="mcell"+(on?" mcell--on":"");
     var b=c.querySelector(".mcell__on");
     if(on){
       if(!b){b=el("span","mcell__on","");c.appendChild(b);}
       b.textContent=String(k+1);
     }else if(b&&b.parentNode){b.parentNode.removeChild(b);}
   });}
 function render(){
   grid.innerHTML="";CELLS={};
   cur.items.slice(0,cur.shown).forEach(function(it){
     var c=el("div","mcell");c.title=it.name;
     var im=document.createElement("img");
     im.loading="lazy";im.alt=it.name;im.src=thumbUrl(it.thumb);
     c.appendChild(im);
     c.appendChild(el("div","mcell__n",it.name));
     c.onclick=function(){toggle(it.id);};
     grid.appendChild(c);CELLS[it.id]=c;});
   mark();
   more.innerHTML="";
   if(cur.items.length>cur.shown){
     var b=el("button","btn btn--sm",
       "看更多（还有 "+(cur.items.length-cur.shown)+" 张）");
     b.type="button";
     b.onclick=function(){cur.shown+=PAGE;render();};
     more.appendChild(b);}}
 function save(ids){
   tip.textContent="保存中…";
   fetch("/api/pins",{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},
     body:JSON.stringify({skill:m.id,mood:cur.mood,ids:ids})})
   .then(function(r){return r.json();}).then(function(d){
     if(d&&d.ok){cur.ids=d.pins||[];mark();
       tip.textContent="已保存：「"+cur.mood+"」钉了 "+cur.ids.length+" 张（最多 8 张）。";}
     else{tip.textContent=(d&&(d.error||d.message))||"没保存成功";}})
   .catch(function(e){tip.textContent="保存失败："+e;});}
 function toggle(id){
   var ids=cur.ids.slice(),i=ids.indexOf(id);
   if(i>=0)ids.splice(i,1);
   else{
     if(ids.length>=8){tip.textContent="一个情绪最多钉 8 张，先取消一张。";return;}
     ids.push(id);}
   cur.ids=ids;mark();save(ids);}
 function load(mood){
   cur.mood=mood;cur.items=[];cur.shown=PAGE;CELLS={};
   grid.innerHTML="";more.innerHTML="";tip.textContent="读图中…";
   /* 梗鲸第一次要拉 2500 张的图库索引，慢的时候四五秒 —— 给句话，别让人以为卡死了 */
   var slow=setTimeout(function(){
     if(cur.mood===mood&&!cur.items.length)
       tip.textContent="还在拉图库索引（梗鲸两千多张，第一次会慢几秒）…";},2500);
   fetch("/api/mood?skill="+encodeURIComponent(m.id)+"&mood="+encodeURIComponent(mood),
     {headers:{"X-Bridge-Token":TOKEN}})
   .then(function(r){return r.json();}).then(function(d){
     clearTimeout(slow);
     if(cur.mood!==mood)return;
     if(!d||!d.ok){tip.textContent=(d&&d.error)||"读取失败";return;}
     cur.ids=(d.pinned||[]).slice();
     cur.items=d.items||[];
     render();
     tip.textContent=(d.note?(d.note+" "):"")+
       "点图钉住（最多 8 张），再点一下取消。钉住的图，这个情绪会优先发它。";})
   .catch(function(e){clearTimeout(slow);tip.textContent="读取失败："+e;});}
 (m.moods||[]).forEach(function(mood){
   var b=el("button","moodchip",mood);b.type="button";
   b.onclick=function(){
     Array.prototype.forEach.call(bar.childNodes,function(n){n.className="moodchip";});
     b.className="moodchip moodchip--on";load(mood);};
   bar.appendChild(b);});
 if(bar.childNodes.length){
   bar.childNodes[0].className="moodchip moodchip--on";
   load(m.moods[0]);}
 return wrap;}

/* 图库来源 + 同步（2026-10-26）：主人问「菲比是本机素材，没有网站吗？不能跟随网站更新嘛」
   —— 图源是 Phoebe Hub（phoebehub.top），这里摆出来，点一下就跟网站对一遍。
   同步是**只增不减**：本地有的一律跳过，不覆盖、不删。 */
function syncPanel(m){
 /* 2026-10-26：菲比图库 / 梗百科词库 / 捏脸素材共用这一块，靠 m.syncKind 决定接口和文案。
    fibi     → /api/fibi_sync，统计「图库 N 张可用」
    memewiki → /api/mw_sync，统计「词库 N 条（生成于 …）」
    petpet   → /api/petpet_sync，统计「模板 13/99（上游还有 86 个没搬）」 */
 var K=m.syncKind||"fibi";
 var C={
   fibi:{url:"/api/fibi_sync",btn:"同步新图",busy:"同步中 ",
     tip:"只补本地没有的：不覆盖、不删你手动丢进去的图。收群里的表情：带图发「收图 名字」。"},
   memewiki:{url:"/api/mw_sync",btn:"更新词库",busy:"更新中 ",
     tip:"只补本地没有的词条：不重抓全量、不覆盖已有。站点限速 0.35 秒/次，新词多时要等一会儿。"},
   petpet:{url:"/api/petpet_sync",btn:"更新素材",busy:"更新中 ",
     tip:"按上游 md5 逐个对：缺的补、改的换、没变的跳过，绝不删。这里只管「已经搬过来的」；"
        +"想加新模板去下面「素材市场」挑，不想要的在那里点「移除」。"}
 }[K]||null;
 if(!C){C={url:"/api/fibi_sync",btn:"同步新图",busy:"同步中 ",tip:""};K="fibi";}
 var URL=C.url, ISW=(K==="memewiki"), ISPP=(K==="petpet");
 var wrap=el("div","");
 var stat=el("p","moodtip","读状态…");
 wrap.appendChild(stat);
 var autoline=el("p","moodtip","");
 if(ISW||ISPP)wrap.appendChild(autoline);
 var bar=el("div","asmore");
 var btn=el("button","btn btn--sm",C.btn);btn.type="button";
 bar.appendChild(btn);
 wrap.appendChild(bar);
 var tip=el("p","moodtip","");
 wrap.appendChild(tip);
 var BASE_TIP=C.tip;
 var TIMER=null;
 function paint(d){
   if(!d||d.ok===false){
     stat.textContent="状态读不到："+((d&&d.error)||"网络不通");
     return;}
   var s;
   if(ISPP){
     s="模板 "+(d.local_ready||0)+"/"+(d.templates||0)+" 个能出图";
     if(d.upstream_total)s+="，上游共 "+d.upstream_total+" 个";
     var miss=(d.upstream_only||[]).length;
     if(miss)s+="（还有 "+miss+" 个没搬）";
   }else if(ISW){
     s="词库 "+(d.count||0)+" 条"+(d.built_at?("，生成于 "+d.built_at):"")+(d.mb?("（"+d.mb+" MB）"):"");
   }else{
     s="图库 "+d.count+" 张可用";
     if(d.collected)s+="（其中群里收的 "+d.collected+" 张）";
   }
   stat.textContent=s;
   if(ISW||ISPP){
     /* 每天自动对一遍：开着就写明时刻，关掉就提示只能手动 */
     var a=d.auto||{};
     var line=a.enabled?("每天 "+("0"+a.hour).slice(-2)+":00 自动对一遍")
                       :"每天自动更新已关（只能点上面的按钮）";
     if(a.enabled&&a.msg)line+="　上次："+a.msg+(a.added?("（+"+a.added+(ISPP?" 个文件":" 条")+"）"):"");
     autoline.textContent=line;
   }
   if(d.running){
     btn.disabled=true;
     btn.textContent=C.busy+(ISPP?((d.checked||0)+"/"+(d.total||"?")):((d.done||0)+"/"+(d.total||"?")));
     tip.textContent=d.msg||"正在拉上游清单…";
   }else{
     btn.disabled=false;btn.textContent=C.btn;
     tip.textContent=(d.msg?d.msg+"　":"")+BASE_TIP;
   }}
 function pull(){
   fetch(URL,{headers:{"X-Bridge-Token":TOKEN}})
   .then(function(r){return r.json();}).then(function(d){
     paint(d);
     if(d&&d.running){TIMER=setTimeout(pull,2000);}
   }).catch(function(e){stat.textContent="状态读不到："+e;});}
 btn.onclick=function(){
   btn.disabled=true;btn.textContent="启动中…";
   fetch(URL,{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify({op:"start"})})
   .then(function(r){return r.json();}).then(function(d){
     paint(d);
     if(!(d&&d.ok))tip.textContent=(d&&(d.error||d.message))||"没启动成功";
     if(TIMER)clearTimeout(TIMER);
     TIMER=setTimeout(pull,1500);
   }).catch(function(e){
     btn.disabled=false;btn.textContent=C.btn;
     tip.textContent="启动失败："+e;});};
 pull();
 return wrap;}

function marketPanel(m){
 /* 素材市场（2026-10-27）。主人问：「为啥模板搬不了，不能在网页控制台预览那些模板，
    然后我看到喜欢的就拿过来，原有的，不喜欢了也可以移除吗」
    → 能。上游 99 个模板这里全列出来：能自动搬的直接「搬过来」；剩下的标「需手工」并写明原因。
    → 移除**不删素材**：挪到 assets/_removed/，随时能放回来。

 2026-10-27 二次改（主人：「点一下就开始闪，黑一下再闪出图又继续黑」）：
   原因是「点图翻帧」只缓存了第一帧，翻到没有的就 404 → 浏览器显示破图。
   现在本机已有素材的模板由服务端**按真参数渲一张示范动图**（/api/petpet_demo），
   <img> 拿到 GIF 自己就会动，不用点也不会闪；没搬的只放大看静态首帧。 */
 var wrap=el("div","");
 var stat=el("p","moodtip","读上游清单…第一次要拉上游源码，十几秒；之后走缓存。");
 wrap.appendChild(stat);
 /* 提示条放在**网格上面**：搬的时候那句「正在搬 xxx 3/9」和搬完的「搬好了」得一眼看见，
    放网格底下要滚半屏才看得到（2026-10-27 主人反馈「点了没反应」其实是提示在下面）。 */
 var tip=el("p","moodtip mktip","缩略图都是动图：搬过的按真参数渲，没搬的用抠出来的参数先渲一遍给你看，抠不出参数的就把原图几帧循环播。点图放大；按「搬过来」才真的下素材。");
 wrap.appendChild(tip);
 var bar=el("div","moodbar");wrap.appendChild(bar);
 var grid=el("div","mgrid mgrid--mk");wrap.appendChild(grid);
 var DATA=[],F="all",BUSY=null,TIMER=null,V=0,STALL=0;   // V=参数表版本号（拼到动图 URL 后面防旧缓存）
 var CH=[["all","全部"],["portable","可搬"],["ready","已搬"],["manual","需手工"]];
 function alive(){return document.body.contains(wrap);}
 function setTip(t,ok){tip.textContent=t;tip.style.color=ok===false?"var(--warn-ink, #c0392b)":"";}

 function paintChips(){
   bar.innerHTML="";
   CH.forEach(function(c){
     var b=el("button","moodchip"+(F===c[0]?" moodchip--on":""),c[1]);
     b.type="button";
     b.onclick=function(){F=c[0];paintChips();paint();};
     bar.appendChild(b);});
   var rf=el("button","moodchip","刷新清单");rf.type="button";
   rf.title="强制重拉上游清单（平时缓存 6 小时）";
   rf.onclick=function(){rf.disabled=true;setTip("重拉上游清单…");
     load(true).then(function(){rf.disabled=false;});};
   bar.appendChild(rf);
 }

 function zoom(src,name){
   /* 点图放大：只有一张，不翻帧（翻帧会闪，见上面注释）。
      src 就是刚显示的那张，服务端给了 max-age=600，浏览器直接走缓存 ⇒ 不会黑一下。 */
   var scrim=el("div","scrim");
   var box=el("div","zoom");
   var im=document.createElement("img");im.src=src;im.alt=name||"";
   box.appendChild(im);
   box.appendChild(el("div","zoom__n",name||""));
   function shut(){
     document.removeEventListener("keydown",onKey,true);
     if(scrim.parentNode)document.body.removeChild(scrim);
   }
   function onKey(e){if(e.key==="Escape"){e.stopPropagation();shut();}}
   document.addEventListener("keydown",onKey,true);
   box.onclick=shut;
   scrim.appendChild(box);
   scrim.onclick=function(e){if(e.target===scrim)shut();};
   document.body.appendChild(scrim);
 }

 function demoUrl(x,r){return "/api/petpet_demo?token="+encodeURIComponent(TOKEN)
   +"&tpl="+x.id+"&v="+V+(r?("&r="+r):"");}
 function frameUrl(x,r){return "/api/petpet_frame?token="+encodeURIComponent(TOKEN)
   +"&tpl="+x.id+"&i=0"+(r?("&r="+r):"");}

 function setImg(im,x){
   /* 有动图就挂动图（服务端按真参数渲的，或者把上游几帧循环播）；
      动图还没做好就挂静态首帧，**不挂空 URL**（空 URL 必 404 → 破图 → 黑闪）。 */
   im._x=x;im._kind=x.demo?"demo":"frame";im.setAttribute("data-tpl",x.id);
   im.onload=function(){if(im._kind==="demo")im.setAttribute("data-anim","1");};
   im.onerror=function(){
     if(im._kind==="demo"){im._kind="frame";im.src=frameUrl(x,Date.now());return;}
     im.setAttribute("data-miss","1");
   };
   im.src=x.demo?demoUrl(x):frameUrl(x);
 }

 function paint(){
   grid.innerHTML="";
   var list=DATA.filter(function(x){return F==="all"||x.status===F;});
   if(!list.length){grid.appendChild(el("p","empty","这一类现在没有。"));return;}
   list.forEach(function(x){
     var c=el("div","mcell"+(BUSY===x.id?" mcell--on":""));
     /* 图还没缓存好就**先不挂 img**：以前挂上去必 404，浏览器画个破图 + 报错，
        主人看到的就是"黑一下再闪出来"。现在留个安静的空位，等后台下完了再重画。 */
     if(!x.thumb&&!x.demo){
       c.appendChild(el("div","mcell__ph","…"));
     }else{
       var im=document.createElement("img");
       im.loading="lazy";im.alt=x.name;
       setImg(im,x);
       im.onclick=function(){zoom(im.currentSrc||im.src,x.name);};
       c.appendChild(im);
     }
     c.appendChild(el("div","mcell__n",x.name));
     var b=el("button","btn btn--sm");b.type="button";
    if(x.removed){
      b.textContent="放回";
      b.disabled=!!BUSY;
      b.onclick=function(){act("restore",x);};
    }else if(x.status==="ready"){
      b.textContent="移除";
      b.onclick=function(){act("remove",x);};
    }else if(x.status==="portable"){
       /* 只有正在搬的那一格写「搬入中…」，别的还是「搬过来」但按不动 ——
          全都写「搬入中…」会让人以为一次搬了一堆。 */
       b.textContent=(BUSY===x.id?"搬入中…":"搬过来");
       b.disabled=!!BUSY;
       b.onclick=function(){act("import",x);};
     }else{
       b.textContent="需手工";b.disabled=true;b.title=x.why||"";
     }
     c.appendChild(b);
     if(x.status==="manual"&&x.why)c.appendChild(el("div","mcell__n",x.why));
     grid.appendChild(c);
   });
   watchThumbs();
 }

 function watchThumbs(){
   /* 缩略图 / 动图都是后台慢慢做的（镜像一张 0.3~1 秒，99 个要几分钟）。
      没到的那几格等它做完再补 —— 只问本地接口（0.2 秒），不点图，所以不会 404 / 破图 / 闪。
      补图时**只换 <img> 的 src**，不重画整页（重画会把已经显示好的图全部重新加载，看着就是闪）。 */
   if(TIMER)clearTimeout(TIMER);
   var pend=DATA.filter(function(x){return !x.thumb||(x.demo_possible&&!x.demo);}).length;
   if(!pend)return;
   TIMER=setTimeout(function(){
     if(!alive())return;
     fetch("/api/petpet_catalog",{headers:{"X-Bridge-Token":TOKEN}})
     .then(function(r){return r.json();}).then(function(d){
       if(!alive()||!d||!d.ok)return;
       var before=DATA;
       DATA=d.items||[];V=d.v||0;
       var byId={};DATA.forEach(function(x){byId[x.id]=x;});
       var repaint=false,changed=0;
       before.forEach(function(o){
         var n=byId[o.id];
         if(!n)return;
         if(!o.thumb&&n.thumb){repaint=true;changed++;}
         if(!o.demo&&n.demo)changed++;
       });
       var ims=grid.querySelectorAll("img[data-tpl]");
       for(var i=0;i<ims.length;i++){
         var im=ims[i],x=byId[im.getAttribute("data-tpl")];
         if(!x||!x.demo||im._kind==="demo")continue;
         im._x=x;im._kind="demo";im.removeAttribute("data-miss");
         im.src=demoUrl(x,Date.now());
       }
       setStat(d);
       if(repaint)paint();else watchThumbs();
       /* 后台任务早跑完了、这一轮也没新东西 ⇒ 别再无脑每 4 秒问一次 */
       if(!changed&&!(d.demos&&d.demos.running)){
         STALL++;
         if(STALL>=3&&!repaint){if(TIMER)clearTimeout(TIMER);}
       }else STALL=0;
     }).catch(function(){if(alive())watchThumbs();});
   },4000);
 }

 function setStat(d){
   var anim=DATA.filter(function(x){return x.demo;}).length;
   var dm=(d&&d.demos)||{};
   stat.textContent="上游 "+d.total+" 个模板：本机 "+d.ready+" 个能用 / "
     +d.portable+" 个能一键搬 / "+d.manual+" 个得手工"
     +(d.removed&&d.removed.length?("；移除过 "+d.removed.length+" 个"):"")
     +"；动图预览 "+anim+"/"+DATA.length
     +(dm.running?("（正在做「"+(dm.name||"")+"」 "+dm.done+"/"+dm.total+"，做好自己就动）")
                 :(anim<DATA.length?("（其余 "+(DATA.length-anim)+" 个只有一张图，没帧可播）"):""));
 }

 function load(refresh){
   var url="/api/petpet_catalog"+(refresh?"?refresh=1":"");
   return fetch(url,{headers:{"X-Bridge-Token":TOKEN}})
   .then(function(r){return r.json();}).then(function(d){
     if(!alive())return;
     if(!d||!d.ok){stat.textContent="上游清单读不到："+((d&&d.error)||"网络不通");return;}
     DATA=d.items||[];V=d.v||0;
     setStat(d);
     paintChips();paint();
   }).catch(function(e){stat.textContent="上游清单读不到："+e;});
 }

 function poll(){
   fetch("/api/petpet_market",{headers:{"X-Bridge-Token":TOKEN}})
   .then(function(r){return r.json();}).then(function(d){
     if(!alive())return;
     if(d&&d.running){
       setTip("正在搬「"+(d.name||"")+"」："+(d.msg||"")+"（"+(d.done||0)+"/"+(d.total||"?")+"）");
       TIMER=setTimeout(poll,1200);return;
     }
     var r=(d&&d.result)||{};
     BUSY=null;
     if(r.ok){
       setTip("搬好了：「"+(r.name||"")+"」，"+((r.detail)||"")+"。现在能用了。",true);
       try{say("素材已入库："+(r.name||""),true);}catch(e){}
     }else{
       setTip("没搬成："+((r.error)||"未知"),false);
     }
     load();
   }).catch(function(){if(alive())TIMER=setTimeout(poll,1500);});
 }

 function act(op,x){
   BUSY=x.id;paint();
   var n=x.files||0;
  setTip(op==="remove"?("正在移除「"+x.name+"」…")
    :(op==="restore"?("正在放回「"+x.name+"」…")
    :("正在搬「"+x.name+"」，"+n+" 张图"
      +(n>30?("——这个图多，得几分钟。抽屉关掉也不影响，回来再看进度"):"")
      +"…")));
   fetch("/api/petpet_market",{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},
     body:JSON.stringify({op:op,name:x.id})})
   .then(function(r){return r.json();}).then(function(d){
     if(!alive())return;
     if(!(d&&d.ok)){
       setTip((d&&(d.error||d.message))||"没成",false);
       BUSY=null;paint();
       /* 服务器那边还有任务在跑（自己连点了两次）⇒ 直接接上轮询，别让人干等 */
       if(d&&/还在搬/.test(d.error||""))poll();
       return;}
    if(op==="import"){poll();}
    else{
      setTip(op==="restore"?("已放回「"+x.name+"」，现在能用了。")
        :("已移除「"+x.name+"」：素材挪到 assets/_removed/，想要了再点「放回」。"),true);
      BUSY=null;load();}
   }).catch(function(e){setTip("请求失败："+e,false);BUSY=null;paint();});
 }

 /* 打开抽屉时如果后台正在搬，直接接上进度（不然连点几次后就再也看不到状态了） */
 fetch("/api/petpet_market",{headers:{"X-Bridge-Token":TOKEN}})
   .then(function(r){return r.json();})
   .then(function(d){
     if(!alive()||!d||!d.running)return;
     BUSY=d.name||"-";setTip("后台正在搬「"+(d.name||"")+"」…");poll();
   }).catch(function(){});

 load();
 return wrap;}

function openPlugin(m,its){
 /* 抽屉挂在 body 上：整页 load() 重画栅格不会把它清掉，改完参数可以接着改。
    抽屉里的行就是普通 row()，所以开关/数字/下拉/多行框的行为跟别处完全一致。 */
 closeDrawer();
 var scrim=el("div","scrim");scrim.onclick=closeDrawer;
 var d=el("div","drawer");d.setAttribute("role","dialog");d.setAttribute("aria-label",m.name);
 var head=el("div","drawer__head");
 head.appendChild(el("div","drawer__title",m.name));
 var x=el("button","btn btn--sm","关闭");x.type="button";x.onclick=closeDrawer;
 head.appendChild(x);d.appendChild(head);
 var body=el("div","drawer__body");
 if(m.desc)body.appendChild(el("p","drawer__note",m.desc));
 if(m.links&&m.links.length){
   /* 主人问「这些从网站选图的插件都不贴网址出来吗」——图库站点/索引 JSON 摆在这里 */
   var lk=el("div","plinks");
   m.links.forEach(function(l){
     var a=document.createElement("a");
     a.href=l.url;a.target="_blank";a.rel="noopener";
     a.textContent=l.label||l.url;lk.appendChild(a);});
   body.appendChild(lk);}
 var params=its.filter(function(i){return i.kind==="param";});
 var prompts=its.filter(function(i){return i.kind==="prompt";});
 function put(it){
   var r=row(it);
   var dd=r.querySelector(".knob__desc");
   if(dd&&it.desc){dd.hidden=false;dd.setAttribute("data-open","1");}
   body.appendChild(r);}
 if(m.sync){
   body.appendChild(el("div","drawer__sec",
     m.syncKind==="memewiki"?"词库来源":(m.syncKind==="petpet"?"素材来源":"图库来源")));
   body.appendChild(syncPanel(m));}
 if(m.syncKind==="petpet"){
   /* 2026-10-27：能预览上游全部 99 个模板、挑喜欢的搬过来、不喜欢的移除 */
   body.appendChild(el("div","drawer__sec","素材市场（上游 99 个，点图能动）"));
   body.appendChild(marketPanel(m));}
 if(m.moods&&m.moods.length){
   body.appendChild(el("div","drawer__sec","情绪标准返图"));
   body.appendChild(moodPanel(m));}
 if(params.length){
   body.appendChild(el("div","drawer__sec","参数"));
   body.appendChild(el("p","drawer__note",
     "存在 config.json 的 skills.settings，不改技能包本体；群里「技能 set "+m.id+" 参数 值」改的是同一处。"));
   params.forEach(put);}
 if(prompts.length){body.appendChild(el("div","drawer__sec","提示词"));prompts.forEach(put);}
 if(!params.length&&!prompts.length)body.appendChild(el("p","empty","这个插件没有可调项。"));
 d.appendChild(body);
 var foot=el("div","drawer__foot");
 foot.appendChild(el("span",null,"改完自动保存，下一句话就生效"));
 var done=el("button","btn btn--primary btn--sm","好了");done.type="button";done.onclick=closeDrawer;
 foot.appendChild(done);d.appendChild(foot);
 document.body.appendChild(scrim);document.body.appendChild(d);
 DRAWER={scrim:scrim,panel:d};
 document.addEventListener("keydown",drawerEsc);}

/* AstrBot 官方市场（只读，2026-10-26）：数据源是 GitHub 上
   AstrBotDevs/AstrBot_Plugins_Collection/plugins.json（约 480KB、1300+ 条）。
   本机只做「逛」—— 那些插件是 AstrBot 框架的 Python 包（metadata.yaml + main.py），
   跟本机「skill.json + 原生 Python 工具」是两套东西，装不进来；所以卡片只给仓库链接。
   联网只发生在点「同步目录」的时候，别在首屏偷偷拉。 */
function astraBlock(g){
 var st=g.astrbot||{};
 var PAGE=30;
 var S={items:[]};
 var box=el("div","asblock");
 var head=el("div","asblock__head");
 head.appendChild(el("span","asblock__title","AstrBot 官方市场"));
 head.appendChild(el("span","tag","只读"));
 var meta=el("span","mk__st",st.count?("已缓存 "+st.count+" 个"):"还没同步过");
 head.appendChild(meta);
 box.appendChild(head);
 box.appendChild(el("p","asblock__note",
   "直连 AstrBot 官方目录（GitHub AstrBotDevs/AstrBot_Plugins_Collection）。"+
   "那些插件是 AstrBot 框架的 Python 包，跟本机的技能包不是一套东西，装不进来 —— "+
   "这里只能逛、看仓库、抄思路。要装到本机，得照 skills/ 的格式重写一遍。"));
 var bar=el("div","asbar");
 var inp=document.createElement("input");
 inp.type="search";inp.placeholder="搜插件：翻译 / 签到 / 语音 / 图片 …";
 bar.appendChild(inp);
 var sel=document.createElement("select");
 sel.title="按分类筛选";
 bar.appendChild(sel);
 var sb=el("button","btn btn--sm","搜索");sb.type="button";bar.appendChild(sb);
 var rb=el("button","btn btn--sm","同步目录");rb.type="button";bar.appendChild(rb);
 box.appendChild(bar);
 var out=el("div","aslist");box.appendChild(out);
 var more=el("div","asmore");box.appendChild(more);
 function fillCats(cats){
   var cur=sel.value||"";
   sel.innerHTML="";
   var all=document.createElement("option");
   all.value="";all.textContent="全部分类"+(st.count?("（"+st.count+"）"):"");
   sel.appendChild(all);
   (cats||[]).forEach(function(c){
     var o=document.createElement("option");
     o.value=c.name;o.textContent=(c.label||c.name)+"（"+c.count+"）";
     sel.appendChild(o);});
   sel.value=cur;if(!sel.value)sel.value="";}
 function row(it){
   var r=el("div","asrow");
   r.appendChild(el("span","asrow__n",it.name||it.id));
   r.appendChild(el("span","asrow__d",it.desc||"（没写简介）"));
   if(it.author)r.appendChild(el("span","asrow__a",it.author));
   if(it.repo){
     var a=document.createElement("a");
     a.href=it.repo;a.target="_blank";a.rel="noopener";a.textContent="仓库";
     r.appendChild(a);}
   return r;}
 function paint(d,append){
   if(!d||!d.items){
     if(!append)out.innerHTML="";
     out.appendChild(el("p","asempty","读取失败。"));return;}
   if(!append){out.innerHTML="";S.items=[];}
   if(d.cats)fillCats(d.cats);
   if(!d.total){
     out.appendChild(el("p","asempty",
       "本地还没有目录缓存 —— 点「同步目录」拉一次（约 480KB，几秒钟）。"));
     more.innerHTML="";return;}
   if(!d.items.length){
     out.appendChild(el("p","asempty","没搜到（目录共 "+d.total+" 个）。换个词或换个分类试试。"));
     more.innerHTML="";return;}
   d.items.forEach(function(it){out.appendChild(row(it));S.items.push(it);});
   more.innerHTML="";
   more.appendChild(el("span","asbar__n","已显示 "+S.items.length+
     " / 命中 "+d.matched+"（目录共 "+d.total+" 个）"));
   if(d.has_more){
     var b=el("button","btn btn--sm","再看 "+PAGE+" 个");b.type="button";
     b.onclick=function(){b.disabled=true;b.textContent="读取中…";pull(true);};
     more.appendChild(b);}}
 function pull(append){
   var off=append?S.items.length:0;
   var u="/api/market?op=astrbot&limit="+PAGE+"&offset="+off+
     "&q="+encodeURIComponent(inp.value||"")+
     "&category="+encodeURIComponent(sel.value||"");
   fetch(u,{headers:{"X-Bridge-Token":TOKEN}})
   .then(function(r){return r.json();}).then(function(d){paint(d,append);})
   .catch(function(e){
     if(!append)out.innerHTML="";
     out.appendChild(el("p","asempty","读取失败："+e));});}
 function sync(){
   rb.disabled=true;rb.textContent="同步中…";
   fetch("/api/market",{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},
     body:JSON.stringify({op:"astrbot_sync",q:inp.value,category:sel.value})})
   .then(function(r){return r.json();}).then(function(d){
     rb.disabled=false;rb.textContent="同步目录";
     if(d&&d.ok){say(d.message||"同步好了",true);
       meta.textContent="已缓存 "+(d.total||0)+" 个";}
     else{say((d&&(d.error||d.message))||"同步失败",false);}
     paint(d,false);})
   .catch(function(e){rb.disabled=false;rb.textContent="同步目录";
     say("同步失败："+e,false);});}
 sb.onclick=function(){pull(false);};
 inp.onkeydown=function(e){if(e.key==="Enter"||e.keyCode===13)pull(false);};
 sel.onchange=function(){pull(false);};
 rb.onclick=sync;
 if(st.count)pull(false);
 else paint({items:[],total:0,q:""});
 return box;}

function marketBody(g){
 /* 插件市场：本地目录（market/）里可一键装进 skills/ 的包。
     已装的可卸（移动不删除）；随 bot 自带的（petpet 等）标「内置」，不给卸载按钮 ——
     它们的工具在本机有原生实现，卸了群口令会哑。 */
 var wrap=el("div","mkwrap");
 var box=el("div","mklist");
 var list=g.market||[];
 if(!list.length){
   box.appendChild(el("p","empty","市场里还没有包：把技能包放进 "+(g.market_dir||"market/")+
     "，再在 plugins.json 里登记一行 id/名字/一句话。"));
 } else list.forEach(function(m){
   var c=el("div","mk"+(m.installed&&m.on?" mk--on":""));
   var top=el("div","mk__top");
   top.appendChild(el("span","mk__name",m.name));
   if(m.builtin)top.appendChild(el("span","tag","内置"));
   else if(m.installed)top.appendChild(el("span","tag"+(m.on?" tag--on":""),m.on?"启用中":"已关"));
   c.appendChild(top);
   c.appendChild(el("div","mk__desc",m.summary||""));
   if(m.tags&&m.tags.length){
     var tg=el("div","mk__tags");
     m.tags.forEach(function(t){tg.appendChild(el("span","tag",t));});
     c.appendChild(tg);}
   var foot=el("div","mk__foot");
   if(m.installed&&!m.removable){
     foot.appendChild(el("span","mk__st","随 bot 自带，不能卸"));
   }else if(m.installed){
     var ub=el("button","btn btn--sm","卸载");ub.type="button";
     ub.setAttribute("data-market",m.id);
     ub.onclick=function(){
       if(window.confirm("卸载「"+m.name+"」？文件会移到 market/_removed/，不会删除，随时能装回来。"))
         marketOp("uninstall",m.id);};
     foot.appendChild(ub);
     foot.appendChild(el("span","mk__st","文件保留在 _removed"));
   }else{
     var ib=el("button","btn btn--primary btn--sm","安装");ib.type="button";
     ib.setAttribute("data-market",m.id);
     ib.onclick=function(){marketOp("install",m.id);};
     foot.appendChild(ib);
     foot.appendChild(el("span","mk__st","装进 skills/，立刻可用"));
   }
   c.appendChild(foot);box.appendChild(c);});
 wrap.appendChild(box);
 wrap.appendChild(astraBlock(g));
 return wrap;}

function marketOp(op,id){
 fetch("/api/market",{method:"POST",headers:{"Content-Type":"application/json",
   "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify({op:op,id:id})})
 .then(function(r){return r.json();}).then(function(d){
   if(d&&d.ok){say(d.message||"好了",true);load();}
   else{say((d&&(d.error||d.message))||"没成功",false);}})
 .catch(function(e){say("请求失败："+e,false);});}

function zoneWall(tiles){
 /* 分区磁贴墙（2026-11-01 总控重构，抄 AstrBot「少量大分区」的思路）：
    一个域一张磁贴 —— 域总开关直接可切、一句话状态、点域名跳进分区改细节。
    这就是「先控制大体」的那个「大体」：所有域的生杀大权一屏看完，细节留在各自分区。 */
 var wall=el("div","ztiles");
 (tiles||[]).forEach(function(t){
   var off=t.toggle&&!t.toggle_on;
   var tile=el("div","ztile"+(off?" ztile--off":""));
   var top=el("div","ztile__top");
   var name=el("button","ztile__name",t.zone);name.type="button";
   name.title="进「"+t.zone+"」分区改细节";
   name.onclick=function(){CURCAT=t.zone;applyCat();
     try{history.replaceState(null,"","#"+encodeURIComponent(t.zone));}catch(e){}};
   top.appendChild(name);
   if(t.toggle){
     var ctl=el("span","ztile__sw");
     var sw=el("button","switch");sw.type="button";
     sw.setAttribute("role","switch");
     sw.setAttribute("aria-checked",t.toggle_on?"true":"false");
     sw.setAttribute("aria-label",t.zone+"总开关");
     sw.appendChild(el("span","switch__dot"));
     sw.onclick=function(){set({key:t.toggle,value:t.toggle_on?"关":"开"});};
     ctl.appendChild(sw);
     ctl.appendChild(el("span","cst",t.toggle_on?"开":"关"));
     top.appendChild(ctl);}
   tile.appendChild(top);
   if(t.stat)tile.appendChild(el("div","ztile__stat",t.stat));
   tile.appendChild(el("div","ztile__meta",(t.groups||[]).length+" 张卡"));
   wall.appendChild(tile);});
 return wall;}

function render(d){
 /* 分区表以后端为准（bot.py ZONES）：重建 CATS / ZONEMAP 后再画卡 ——
    放在第一行，后面所有 data-cat 归属都靠它。 */
 buildZones(d.zones);
 var pf=$("pf");pf.innerHTML="";
 var tiles=el("div","tiles");
 Object.keys(d.profiles||{}).forEach(function(name){
   var kvmap=d.profiles[name]||{};
   var b=el("button","tile");b.type="button";
   var top=el("span","tile__top");
   top.appendChild(el("span","tile__name",name));
   if(matchProfile(d,kvmap))top.appendChild(el("span","tag tag--on","当前"));
   b.appendChild(top);
   b.appendChild(el("span","tile__kv",Object.keys(kvmap).map(function(k){
     return k+"="+kvmap[k];}).join("　")));
   b.onclick=function(){say("正在应用预设档「"+name+"」…",true);set({profile:name});};
   tiles.appendChild(b);});
 pf.appendChild(tiles);
 /* 重画前先清掉上一轮 JS 生成的卡 —— 概览/预设档/提示条是写在 HTML 里的静态卡，
    不带 .js-card 标记，留着不删（否则每次 render 都会在栅格里堆一份重复卡）。 */
 var grid=$("grid");
 Array.prototype.slice.call(grid.querySelectorAll(".card.js-card"))
   .forEach(function(c){c.parentNode.removeChild(c);});
 CARDS=[];
 /* 画图档那项带了 lib（工作流库）就插一张磁贴卡，紧跟在「现画」后面 */
 var groups=(d.groups||[]).slice();var LIB=null;
 groups.forEach(function(g){(g.items||[]).forEach(function(i){if(i.lib)LIB=i;});});
 if(LIB&&LIB.lib&&LIB.lib.length){
   var at=0;groups.forEach(function(g,gi){if(g.group==="现画")at=gi+1;});
   groups.splice(at,0,{group:"工作流库",items:[],lib:LIB.lib,texts:LIB.lib_texts||[],
                       lib_item:LIB,min_span:2});}
 /* 人格管理卡（2026-10-25）：人设增删改查 —— 名字和正文都能改、能存、能直接切。
    2026-10-27 主人：「功能重复了，不要让一个功能有两处控制方式」——
    原来「人格」组里还有一个 ai.role 下拉 + ai.system 大文本框，跟这张卡干的是同一件事，
    两处各改各的、谁也不刷新谁。现在 ai.role / ai.system 在 bot.py 里标了 web_hidden
    （口令和 MCP 照旧能用），网页上**只有这张卡**能动人设。
    位置也从「系统」分区搬到「人格」组前面 —— 人格的事留在对话分区，进门第一张就是它。 */
 var per=-1;groups.forEach(function(g,gi){if(g.group==="人格")per=gi;});
 if(per<0)per=0;
 groups.splice(per,0,{group:"人格管理",items:[],persona:true,min_span:2});
 /* 前端也摘一道：bot.py 里 ai.role / ai.system 已标 web_hidden（后端过滤），
    但改后端要重启 bot 才生效；模板是每次请求现读的，摘在这儿刷新页面就能看到，
    不用等重启，也不用担心两个版本短暂不一致。 */
 groups.forEach(function(g){
   if(g.group!=="人格")return;
   g.items=(g.items||[]).filter(function(it){
     return it.key!=="ai.role"&&it.key!=="ai.system";});});
 /* 成员白名单卡：跟在工作流库后面（没工作流库就跟在现画后面） */
 /* 会话总控卡（2026-10-26 主人要求）：一张表管**群聊和私聊** ——
    响应 / 插话 / ComfyUI / Moss / 本地工具 / 人格。取代原来只管两条生图链路的
    「生图链路」卡（2026-10-24），单独一个「总控」分区，进门第一眼就是它。
    磁贴墙插在它前面：总控页顺序 = 概览 → 预设档 → 分区磁贴墙 → 会话总控。 */
 groups.splice(0,0,{group:"会话总控",items:[],sess:true,min_span:2});
 groups.splice(0,0,{group:"分区磁贴",items:[],ztiles:d.tiles||[],min_span:3});
 /* 群聊记录卡（2026-09-29）：选群看聊天 + 未监控群一键加入。
    2026-11-01 总控重构：从页首挪到「群务」卡前面 —— 看记录本来就是群务的一部分，
    别再占总控页（总控页 = 概览 + 预设档 + 会话总控 + 分区磁贴墙）。 */
 var gwat=0;groups.forEach(function(g,gi){if(g.group==="群务")gwat=gi;});
 groups.splice(gwat,0,{group:"群聊记录",items:[],chat:true,min_span:1});
 /* 成员白名单卡：跟在群聊记录后面，同属「群友」分区 */
 groups.splice(gwat+1,0,{group:"成员白名单",items:[],wl:true,min_span:1});
 /* 群聊能力卡（2026-09-27）：识别图片/表情、戳一戳、引用回复、转发聊天记录、不时发图——
    有开关的指路「群友」卡，纯行为标「自动」，主人一眼看全，不用翻代码猜功能。 */
 var cap=0;groups.forEach(function(g,gi){if(g.group==="群友")cap=gi;});
 groups.splice(cap,0,{group:"群聊能力",info:[
  ["识别图片和表情","群友只发图或表情（含商城表情 mface）→ 视觉模型看一眼，按当前人设随口反应一句。@它发图必看；没@按概率触发，概率/冷却开关在「群友」卡。","有开关"],
  ["戳一戳","谁戳谁、谁被戳都会记进上下文（它知道群里在互戳）；被戳自动戳回去，120 秒最多一次，30% 概率补一句嘴（回戳开关在「群友」卡）；群里发「戳 @某人」它就去戳那个人。","部分有开关"],
  ["引用回复","它回复谁就引用谁的消息——画图、看图闲聊、AI 回复全部带引用段，群里能对上话。","自动"],
  ["识别转发聊天记录","收到合并转发自动取原文摊平成「【转发聊天记录·共N条】A: xx / B: xx」进上下文，@它还能聊里面的内容。","自动"],
  ["不时发图","没人说话也会按概率往群里丢一张图库里的图（发图概率 / 冷却开关在「群友」卡）。","有开关"]
 ],min_span:1});
 /* 生图总开关（design.md §5.23）：页头常驻，值就取自「现画」那一项，不另开接口 */
 var draw=null;(d.groups||[]).forEach(function(g){(g.items||[]).forEach(function(i){
   if(i.type==="bool"&&(i.label==="现画"||i.label==="画图"))draw=i;});});
 DRAWOFF=draw?!draw.value:false;
 var mw=$("mastersw");
 if(draw){
   mw.hidden=false;
   var mb=$("msw");
   mb.setAttribute("aria-checked",draw.value?"true":"false");
   mb.onclick=function(){set({key:draw.label,value:draw.value?"关":"开"});};
   var ms=$("mswst");
   ms.textContent=draw.value?"开":"关";
   ms.className="mastersw__st"+(draw.value?" mastersw__st--on":"");}
 else{mw.hidden=true;}
 groups.forEach(function(g,gi){
   var items=g.items||[];
   var bs=g.group==="大脑"?brainSplit(items):null;
   var card=el("section","card js-card");
   card.setAttribute("data-cat",catOf(g.group));
   var head=el("div","card__head");
   head.appendChild(el("span","badge",pad(gi+3)));
   head.appendChild(el("h2","card__title",g.title||g.group));
   /* 卡头右侧那行小字：说清「这卡里有多少东西」。
      插件卡报「N 个插件」、市场卡报「N 个包」—— 报旋钮数（22 项）没人看得懂。
      2026-10-27：原来是七层嵌套三元，加一条「人格管理」少写一个右括号，整个脚本就静默挂掉
      （页面只剩静态骨架、状态栏卡在「连接中…」）。改成顺序 if 返回，以后加分支不用再数括号。 */
   var nVis=(bs?bs.vis:items).filter(function(it){return !it.web_hidden;}).length;
   var metaTxt=(function(){
     if(g.lib)return g.lib.length+" 档";
     if(g.persona)return "选 / 改 / 切人设";
     if(g.wl)return "管理员名单";
     if(g.sess)return "群聊 + 私聊";
     if(g.chat)return "选群看记录";
     if(g.ztiles)return "先管大体，再管细节";
     if(g.plugins)return g.plugins.length+" 个插件";
     if(g.market)return g.market.length+" 个包";
     return (g.info?g.info.length:nVis)+" 项"+(bs&&bs.hid?"（收起 "+bs.hid+" 项）":"");
   })();
   head.appendChild(el("span","card__meta",metaTxt));
   card.appendChild(head);
   var rows=el("div","rows");
   if(g.wl){rows.appendChild(wlBody());}
   else if(g.lib&&g.lib.length){
     rows.appendChild(libTiles(g.lib,g.lib_item?[g.lib_item]:items));
     if(g.texts&&g.texts.length)rows.appendChild(textSlots(g.texts));}
   else if(g.group==="现画"){renderDraw(rows,items);}
   else if(g.group==="会话总控"){rows.appendChild(sessionCard(g));}
   else if(g.ztiles){rows.appendChild(zoneWall(g.ztiles));}
   else if(g.group==="人格管理"){rows.appendChild(personaCard(g));}
   else if(g.group==="插件列表"){rows.appendChild(pluginList(g));}
   else if(g.group==="插件市场"){rows.appendChild(marketBody(g));}
   else if(g.info){infoRows(g.info,rows);}
   else if(g.chat){rows.appendChild(chatBody());}
   else{
     /* 2026-11-01 总控重构「先管大体」：开关（bool）和档位（enum）留在主区，
        数值/文本这类调参的细节收进卡尾「高级参数」折叠 —— 默认收着，要用再展开。
        multiline（人设正文）不算细节，secret（密钥）也别藏，主人得一眼看到配没配。 */
     var main=[],adv=[];
     (bs?bs.vis:items).forEach(function(it){
       if(it.web_hidden)return;
       /* depends_on（2026-10-30）：被某个开关控制显隐的项。
          「识图跟随聊天模型」一开，「识图模型」下拉就收起来 ——
          否则两个模型控件同时摆着，主人分不清到底用的哪个（10-29 骂过一次）。
          判断用「当前表单里的值」，不是服务端快照值，这样点了开关马上跟着变。 */
       if(depHidden(it))return;
       if((it.type==="int"||it.type==="float"||(it.type==="str"&&!it.multiline))
          &&it.key!=="ai.system"){
         adv.push(it);return;}
       main.push(it);});
     main.forEach(function(it){rows.appendChild(row(it));});
     if(adv.length)rows.appendChild(advFold(adv));
     if(!main.length&&!adv.length)rows.appendChild(el("p","empty","暂无开关"));
     /* 识图组：把「最后一次是谁答的 / 别的档为什么没答上」摊开给主人看。
        数据来自 /api/config 的 see_last（reverser.last_chain()，只留最近一条）。 */
     if(g.group==="识图"){rows.appendChild(seeDiag(g));}}
   card.appendChild(rows);grid.appendChild(card);
   CARDS.push({el:card,cat:card.getAttribute("data-cat"),
               n:((g.chat||g.info)?1:items.length),ms:g.min_span||0});});
 /* 静态卡（概览/预设档）与生图提示条也要进 CARDS，才能跟着分类一起显隐与排布 */
 ["ovcard","pfcard"].forEach(function(id){var c=$(id);
   if(c)CARDS.push({el:c,cat:"总控",n:6,ms:0});});
 var nt=$("drawoff");
 if(nt)CARDS.push({el:nt,cat:"生图",notice:true,ms:0});
 /* 建完就按当前分类显隐 + 排布（页面初次进来默认停在「总控」）
    深链：URL 带 #对话/#生图/… 就直接打开那一类（分区名现在是中文，hash 要解码） */
 var _h="";
 try{_h=decodeURIComponent((location.hash||"").replace("#",""));}
 catch(e){_h=(location.hash||"").replace("#","");}
 if(CATKEYS.indexOf(_h)>=0)CURCAT=_h;
 buildCatNav();applyCat();
 /* 说明开关：每次重画完都按当前 DESCON 重新同步一次（新建的折叠块要跟着开合） */
 setDesc(DESCON);
 /* 深链搜索：/admin?q=冷却 直接进搜索态（可书签；界面核验脚本也走这条） */
 try{var _q=new URLSearchParams(location.search).get("q");
   if(_q){var _b=$("searchbox");if(_b){_b.value=_q;applySearch();}}}catch(e){}}
function clearSearch(){
 /* 退出搜索态：清词、清命中标记、收起搜索时强行展开的高级折叠，回到原分区。
    行级显隐只靠 body.searching 的 CSS，不动行本身 —— 恢复零风险。 */
 SEARCHQ="";
 var box=$("searchbox");if(box&&box.value)box.value="";
 document.body.classList.remove("searching");
 var hs=document.querySelectorAll("#grid .knob.qhit");
 for(var i=0;i<hs.length;i++)hs[i].classList.remove("qhit");
 var fs=document.querySelectorAll("#grid details.fold--adv");
 for(var j=0;j<fs.length;j++)fs[j].open=false;
 var st=$("searchst");if(st)st.textContent="";
 applyCat();}
function applySearch(){
 /* 顶部搜索（抄 AstrBot「搜索定位配置项」）：197 个旋钮平铺着，靠翻分区找太慢。
    命中行挂 .qhit，CSS 把非命中行藏掉；含命中的卡跨分区显示（applyCat 的搜索分支）。
    命中「高级参数」折叠里的行时折叠要展开，不然搜到了也看不见。 */
 var box=$("searchbox");SEARCHQ=((box&&box.value)||"").trim().toLowerCase();
 var on=!!SEARCHQ;
 document.body.classList.toggle("searching",on);
 if(!on){clearSearch();return;}
 var fs=document.querySelectorAll("#grid details.fold--adv");
 for(var f=0;f<fs.length;f++)fs[f].open=true;
 var hits=0;
 var rows=document.querySelectorAll("#grid .knob[data-search]");
 for(var i=0;i<rows.length;i++){
   var hit=rows[i].getAttribute("data-search").indexOf(SEARCHQ)>=0;
   rows[i].classList.toggle("qhit",hit);if(hit)hits++;}
 applyCat();
 var st=$("searchst");
 if(st)st.textContent=hits?("命中 "+hits+" 项"):"没有匹配的开关";}
function applyCat(){ /* 只切 hidden，不重画 DOM（重建会清掉别处没保存的输入）；切完按「可见卡片」重算 span，
    否则被隐藏的卡还占着格子，整页会留空档。
    搜索态（SEARCHQ 非空）走另一条路：跨分区显示所有含命中行的卡。 */
 var cols=colsNow(),vis=[],gs=[];
 if(SEARCHQ){
   CARDS.forEach(function(c){
     var on=!c.notice&&!!c.el.querySelector(".knob.qhit");
     c.el.hidden=!on;
     if(on){vis.push(c);gs.push({items:new Array(c.n),min_span:c.ms});}});
   var spans=planSpans(gs,cols);
   vis.forEach(function(c,i){c.el.style.setProperty("--span",String(spans[i]||1));});
   return;}
 CARDS.forEach(function(c){
   var on=c.notice?(DRAWOFF&&CURCAT==="生图"):(c.cat===CURCAT);
   c.el.hidden=!on;
   if(on){vis.push(c);gs.push({items:new Array(c.n),min_span:c.ms});}});
 var spans=planSpans(gs,cols);
 vis.forEach(function(c,i){c.el.style.setProperty("--span",String(spans[i]||1));});
 syncCatNav();}
function buildCatNav(){
 /* 侧栏分区按钮。**没有「全部」**。只在整页重画时建一次（点击不再重建，避免丢焦点）。
    计数让"这类有多少项"不点开也看得见；生图总开关关着时在「生图」上挂一个「关」角标。
    每个按钮带 data-cat / data-name，供切换与核验脚本按语义取用，不用去猜文本。 */
 var nav=$("catnav");if(!nav)return;
 var cnt={};CATS.forEach(function(c){cnt[c.key]=0;});
 CARDS.forEach(function(c){if(!c.notice&&cnt[c.cat]!==undefined)cnt[c.cat]++;});
 nav.innerHTML="";
 CATS.forEach(function(c){
   var b=el("button","catbtn");b.type="button";
   b.setAttribute("data-cat",c.key);b.setAttribute("data-name",c.name);
   b.appendChild(document.createTextNode(c.name));
   b.appendChild(el("span","catbtn__n",cnt[c.key]+" 项"));
   if(c.key==="生图"&&DRAWOFF)b.appendChild(el("span","catbtn__off","关"));
   b.onclick=function(){clearSearch();if(CURCAT!==c.key){CURCAT=c.key;applyCat();
     try{history.replaceState(null,"","#"+encodeURIComponent(c.key));}catch(e){}}};
   nav.appendChild(b);});
 syncCatNav();}
function syncCatNav(){
 var bs=document.querySelectorAll("#catnav .catbtn");
 for(var i=0;i<bs.length;i++){
   /* 搜索态下没有「当前分区」这回事，侧栏全部取消选中 */
   var sel=SEARCHQ?"":CURCAT;
   bs[i].setAttribute("aria-selected",
     bs[i].getAttribute("data-cat")===sel?"true":"false");}}
function pad(n){return (n<10?"0":"")+n;}
function personaCard(g){
 /* 人格管理（2026-10-25 主人要）：人设 = roles/*.md 一个文件一个。
    选一个 → 改名/改正文 → 保存（含改名）、新建、删除、直接切换。
    保存的如果是当前正在用的那个，会立刻生效并清掉旧的对话上下文。
    2026-10-27：这是网页上**唯一**的人设入口 —— 「人格」组里的 ai.role / ai.system
    已经 web_hidden，避免同一个开关摆两处、改一边另一边不跟着动。 */
 var box=el("div","slots");
 var r=el("div","knob");
 var top=el("div","knob__top");
 var lab=el("div","knob__lab");
 lab.appendChild(el("span","knob__name","人设列表"));
 lab.appendChild(el("span","tag","唯一入口"));
 lab.appendChild(el("span","tag","roles/*.md"));
 top.appendChild(lab);r.appendChild(top);
 var d=el("div","knob__desc");
 d.textContent="网页上改人格只在这里。选一个载入 → 改名字（= 改名，旧文件删掉）/ 改正文（= 覆盖保存）"
  +"→ 点「保存」；想让群里立刻换成它，再点「切换到此」。"
  +"保存或切换的正好是当前在用的那个，会立刻生效并清空旧对话上下文。"
  +"「自定义」不是一个文件，就是当前正文本身，选中它直接编辑即可。"
  +"群里发「切换X人格」也能换，跟这里的「切换到此」是同一条路。";
 r.appendChild(d);

 var row1=el("div","wladd");row1.style.marginTop="8px";
 var sel=document.createElement("select");sel.className="field";sel.style.flex="1";
 var btNew=el("button","btn","新建");btNew.type="button";
 row1.appendChild(sel);row1.appendChild(btNew);r.appendChild(row1);

 var row2=el("div");
 row2.style.cssText="display:flex;gap:8px;margin-top:8px;align-items:center";
 var nameIn=document.createElement("input");nameIn.type="text";nameIn.className="field txt";
 nameIn.placeholder="人设名（改这里就是改名）";nameIn.style.flex="1";
 var btSave=el("button","btn","保存");btSave.type="button";
 var btUse=el("button","btn","切换到此");btUse.type="button";
 var btDel=el("button","btn","删除");btDel.type="button";
 row2.appendChild(nameIn);row2.appendChild(btSave);
 row2.appendChild(btUse);row2.appendChild(btDel);r.appendChild(row2);

 var ta=document.createElement("textarea");ta.className="field txtarea";ta.rows=14;
 ta.style.marginTop="8px";ta.style.width="100%";
 r.appendChild(ta);
 var stat=el("p","empty","");r.appendChild(stat);
 box.appendChild(r);

 var ST={list:[],active:"",orig:null};
 function post(payload){
   return fetch("/api/personas",{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify(payload)})
   .then(function(rr){return rr.json();});
 }
 function note(t){stat.textContent=t||"";}

 function drawSel(){
   sel.innerHTML="";
   ST.list.forEach(function(it){
     var o=document.createElement("option");
     o.value=it.name;
     o.textContent=it.name+(it.active?"（当前）":"")+"  "+it.chars+"字";
     if(ST.orig===it.name)o.selected=true;
     sel.appendChild(o);
   });
 }
 function load(name){
   if(!name)return;
   post({op:"read",name:name}).then(function(dd){
     if(!dd||!dd.ok){note((dd&&dd.error)||"读不到");return;}
     ST.orig=dd.name;nameIn.value=dd.name;ta.value=dd.content||"";
     note("已载入「"+dd.name+"」（"+((dd.content||"").length)+" 字）");
   }).catch(function(e){note("请求失败："+e);});
 }
 sel.onchange=function(){load(sel.value);};
 btNew.onclick=function(){
   ST.orig=null;nameIn.value="";ta.value="";
   nameIn.focus();note("填个名字和正文，点「保存」就是新人设");
 };
 btSave.onclick=function(){
   var nm=nameIn.value.trim();
   if(!nm){note("名字不能空");return;}
   if(!ta.value.trim()){note("正文不能空");return;}
   btSave.disabled=true;
   post({op:"save",name:nm,content:ta.value,old_name:ST.orig||""}).then(function(dd){
     btSave.disabled=false;
     if(!dd||!dd.ok){note((dd&&dd.error)||"没存成");return;}
     var m="已保存「"+dd.name+"」";
     if(dd.renamed)m+="（原「"+ST.orig+"」已改名）";
     if(dd.active)m+="，并且立刻生效（旧对话上下文已清空）";
     note(m);ST.orig=dd.name;refresh();
   }).catch(function(e){btSave.disabled=false;note("请求失败："+e);});
 };
 btUse.onclick=function(){
   var nm=nameIn.value.trim()||sel.value;
   if(!nm){note("先选一个");return;}
   fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},
     body:JSON.stringify({key:"ai.role",value:nm})})
   .then(function(rr){return rr.json();}).then(function(dd){
     if(dd&&dd.ok){note("已切到「"+nm+"」");refresh();}
     else{note((dd&&dd.error)||"没切成");}
   }).catch(function(e){note("请求失败："+e);});
 };
 btDel.onclick=function(){
   var nm=ST.orig||sel.value;
   if(!nm){note("先选一个");return;}
   if(!confirm("删掉人设「"+nm+"」？不能撤销。"))return;
   post({op:"delete",name:nm}).then(function(dd){
     if(dd&&dd.ok){note("已删除「"+nm+"」");ST.orig=null;nameIn.value="";ta.value="";refresh();}
     else{note((dd&&dd.error)||"没删成");}
   }).catch(function(e){note("请求失败："+e);});
 };
 function refresh(){
   post({op:"list"}).then(function(dd){
     if(!dd||!dd.ok){note((dd&&dd.error)||"读不到人设");return;}
     ST.list=dd.list||[];ST.active=dd.active||"";
     drawSel();
     if(!ST.orig&&ST.list.length){
       var a=ST.list.filter(function(x){return x.active;})[0]||ST.list[0];
       load(a.name);
     }
   }).catch(function(e){note("请求失败："+e);});
 }
 refresh();
 return box;}

function sessionCard(g){
 /* 会话总控（2026-10-26 主人要求）：一张表管**群聊和私聊**，取代只管网条生图链路的旧卡。
    版式纪律（主人原话：「界面直观简洁，不堆叠」）：
      · 一张表、一行一个会话，开关全横着排；不折叠、不分小节、不加批量按钮；
      · 私聊单独一行放最上面（所有私聊共用一个开关）；
      · 写回各回各家，不开第二份真相：
          响应 → groups 白名单（私聊 → private.enabled）
          ComfyUI / Moss → draw.<engine>.groups（私聊 → draw.<engine>.private）
          插话 / 工具 / 人格 → session.*（qbot_session.py）
    列含义：
      响应   在这个会话理不理人
      插话   没被@也主动接话（仿真群友 + 冷场开场；私聊没有这回事，画「—」）
      工具   本地工具（agent 大脑 + 插件提示词）；关掉只走纯 API 大脑
      人格   这个会话用哪个人格，默认「跟随全局」 */
 var box=el("div","slots");
 var r=el("div","knob");
 var bar=el("div","wladd");
 bar.style.cssText="margin:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap";
 var rf=el("button","btn","刷新群列表");rf.type="button";
 var pf=el("button","btn","");pf.type="button";
 var tip=el("span","");
 tip.style.cssText="margin-left:auto;font-size:12px;opacity:.55;white-space:nowrap;"
   +"overflow:hidden;text-overflow:ellipsis;max-width:60%";
 bar.appendChild(rf);bar.appendChild(pf);bar.appendChild(tip);
 r.appendChild(bar);
 var tbl=el("div","");tbl.style.marginTop="10px";r.appendChild(tbl);
 var d=el("div","knob__desc");
 d.textContent="一行一个会话，勾上就开、不勾就关。"
  +"「响应」= 在这个会话理不理人；「插话」= 没被@时会不会自己接话；"
  +"ComfyUI / Moss 是两条生图链路，都关就画不了；"
  +"「工具」关掉后只走纯 API 大脑（不跑本地 agent、不注入插件提示词）；"
  +"人格留「跟随全局」就用当前全局人设。私聊**按人一行**（只列聊过的人），"
  +"每个人的开关互不影响；私聊总开关（关掉全部私聊）在「私聊」分区。";
 r.appendChild(d);
 box.appendChild(r);
 var ST={rows:[],pref:"comfyui",personas:[],gp:""};
 var COLS=[["会话","1","left"],["响应","48px","center"],["插话","48px","center"],
           ["ComfyUI","64px","center"],["Moss","54px","center"],
           ["工具","48px","center"],["人格","132px","center"]];
 /* 列宽必须写死成 0 0 <w>：`flex:46px` 在 CSS 里等于 grow=1，几列会各自撑开，
    表头和数据就对不上（2026-10-26 实测截图踩到）。 */
 function fx(node,w){node.style.flex=(w==="1")?"1 1 0%":("0 0 "+w);node.style.minWidth="0";}

 function post(payload){
   return fetch("/api/session_control",{method:"POST",headers:{"Content-Type":"application/json",
     "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify(payload)})
   .then(function(rr){return rr.json();});
 }
 function drawPref(){
   pf.textContent="优先："+(ST.pref==="moss"?"Moss":"ComfyUI");
   pf.title="两条链路都开时走哪条";
   pf.onclick=function(){
     var v=(ST.pref==="moss")?"comfyui":"moss";
     post({engine_pref:v}).then(function(dd){
       if(dd&&dd.ok){ST.pref=v;drawPref();drawTable();
         say("两条都开时优先走 "+(v==="moss"?"Moss":"ComfyUI"),true);}
       else{say((dd&&dd.error)||"没改成",false);}})
     .catch(function(e){say("请求失败："+e,false);});};
 }
 function setCell(row,field,val,ui){
   if(ui)ui.disabled=true;
   post({gid:row.gid,field:field,value:val}).then(function(dd){
     if(ui)ui.disabled=false;
     if(dd&&dd.ok){
       if(field==="persona")row.persona=val;else row[field]=val;
       if(field==="comfyui"||field==="moss"){
         row.engine_here=(row.comfyui&&row.moss)?ST.pref:(row.moss?"moss":(row.comfyui?"comfyui":null));}
       drawTable();say("已保存",true);
     }else{
       if(ui&&ui.type==="checkbox")ui.checked=!val;
       say((dd&&dd.error)||"没改成",false);}})
   .catch(function(e){
     if(ui)ui.disabled=false;
     if(ui&&ui.type==="checkbox")ui.checked=!val;
     say("请求失败："+e,false);});
 }
 function cellHead(txt,w,align){
   var s=el("span","",txt);fx(s,w);s.style.textAlign=align||"center";
   s.style.fontSize="12px";s.style.opacity=".55";return s;
 }
 function checkbox(row,field,w){
   var box=el("span","");fx(box,w);box.style.textAlign="center";
   if(row[field]===null||row[field]===undefined){
     var dash=el("span","","—");dash.style.opacity=".35";box.appendChild(dash);return box;}
   var cb=document.createElement("input");cb.type="checkbox";cb.checked=!!row[field];
   cb.onchange=function(){setCell(row,field,cb.checked,cb);};
   box.appendChild(cb);return box;
 }
 function personaSel(row){
   var w=el("span","");fx(w,"132px");w.style.textAlign="center";
   var sel=document.createElement("select");sel.className="field";
   sel.style.cssText="width:126px;height:26px;font-size:12px;padding:0 4px";
   var o0=document.createElement("option");
   o0.value="";o0.textContent="跟随全局"+(ST.gp?("（"+ST.gp+"）"):"");
   sel.appendChild(o0);
   ST.personas.forEach(function(nm){
     var o=document.createElement("option");o.value=nm;o.textContent=nm;sel.appendChild(o);});
   sel.value=row.persona||"";
   sel.onchange=function(){setCell(row,"persona",sel.value,sel);};
   w.appendChild(sel);return w;
 }
 function drawTable(){
   tbl.innerHTML="";
   if(!ST.rows.length){tbl.appendChild(el("p","empty","没有会话。点「刷新群列表」从 NapCat 拉一次。"));return;}
   var hd=el("div","");
   hd.style.cssText="display:flex;gap:8px;align-items:center;padding-bottom:6px";
   COLS.forEach(function(c){hd.appendChild(cellHead(c[0],c[1],c[2]));});
   tbl.appendChild(hd);
   ST.rows.forEach(function(row){
     var line=el("div","");
     line.style.cssText="display:flex;gap:8px;align-items:center;padding:6px 0;"
       +"border-top:1px solid rgba(255,255,255,.07)";
     var nm=el("span","");
     nm.style.cssText="flex:1;min-width:0;font-size:13px;overflow:hidden;"
       +"text-overflow:ellipsis;white-space:nowrap";
     nm.textContent=row.private?(row.name||("QQ "+row.uid)):(row.name||"(无名群)");
     var sub=el("span","","　"+(row.private?("私聊·"+row.uid):row.gid)
       +(row.engine_here?("　"+(row.engine_here==="moss"?"Moss":"ComfyUI")):"　—"));
     sub.style.cssText="font-size:11.5px;opacity:.45";
     nm.appendChild(sub);
     line.appendChild(nm);
     [["ai","48px"],["sim","48px"],["comfyui","64px"],["moss","54px"],["tools","48px"]]
       .forEach(function(p){line.appendChild(checkbox(row,p[0],p[1]));});
     line.appendChild(personaSel(row));
     tbl.appendChild(line);
   });
 }
 function load(){
   say("正在读会话列表…",true);
   post({}).then(function(dd){
     if(!(dd&&dd.ok)){say((dd&&dd.error)||"读不到会话列表",false);return;}
     ST.rows=dd.rows||[];ST.pref=dd.engine_pref||"comfyui";
     ST.personas=dd.personas||[];ST.gp=dd.global_persona||"";
     drawPref();drawTable();
     var np=ST.rows.filter(function(r){return r.private;}).length;
     var ng=ST.rows.length-np;
     tip.textContent=(dd.private_enabled?"":"私聊总开关关着 · ")
       +"私聊 "+np+" · 群 "+ng+" · Moss "+(dd.moss_ready?"就绪":"没配 Key");
     say("拿到 "+ST.rows.length+" 个会话",true);
   }).catch(function(e){say("请求失败："+e,false);});
 }
 rf.onclick=load;
 drawPref();drawTable();
 /* 首次进页面自动拉一次（主人：不想为了看一眼还得先点刷新）。
    只自动拉一次 —— 整页每次 config 刷新都会重画，每次都去问 NapCat 太浪费；
    要最新的点「刷新群列表」。 */
 if(!window._sessLoaded){window._sessLoaded=1;load();}
 return box;}

function seeProbe(btn){
 /* 「重新探活」按钮（2026-10-25）：真给每档发一次极小请求，看谁还活着。

    🔴 为什么必须单独按：一轮探活要 4~5 秒（每档都真发请求），
    挂在 /api/config 首屏上会让人白等。所以首屏只读上次缓存，
    要看最新的就点这里。探完直接 load() 重画整页，诊断块跟着刷新。 */
 if(btn){btn.disabled=true;btn.textContent="探活中…";}
 say("正在逐个探活视觉后端（约 4~5 秒）…",true);
 fetch("/api/see_probe",{method:"POST",headers:{"Content-Type":"application/json",
   "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify({force:true})})
 .then(function(r){return r.json();}).then(function(d){
   if(btn){btn.disabled=false;btn.textContent="重新探活";}
   if(d&&d.ok&&d.diag){say("探活完成："+(d.diag.verdict||""),true);load();}
   else{say((d&&d.error)||"探活没成",false);}})
 .catch(function(e){
   if(btn){btn.disabled=false;btn.textContent="重新探活";}
   say("请求失败："+e,false);});}
function seeDiag(g){
 /* 识图诊断块（2026-10-25 重做）：结论 + 网页那一段 + 接力链健康 + 最近一次接力。
    只读，不可点。

    🔴 为什么重做：旧版只列「链的顺序」和「上次谁答的」，**看不出每档死活**。
    2026-10-25 实测的实情是链上 7 档里 6 档是死的（硅基 402 / DeepSeek 402 /
    智谱×3 是 401），只剩魔搭一档 —— 而旧面板上一切看起来都正常，
    主人只能靠「@它发图它不理人」来发现。现在健康状态由 bot 侧 see_diagnosis() 探出来，
    这里负责标红/标黄。
    数据来源：/api/config 的 g.see_diag（bot.see_diagnosis()）。 */
 var box=el("div","slots");
 var dg=g.see_diag||{};

 /* ① 一句话结论 —— 放最前面，别的都可以不看，这行必须能看懂 */
 var r0=el("div","knob knob--ro");
 var t0=el("div","knob__top");var l0=el("div","knob__lab");
 l0.appendChild(el("span","knob__name","现在能不能识图"));
 var alive=(dg.alive===undefined?null:dg.alive),total=(dg.total||0);
 var vt=(dg.verdict||"（还没算出来）");
 var tgTag=el("span","tag",alive===null?"?":(alive+"/"+total+" 档可用"));
 if(alive!==null){
   tgTag.style.color = alive===0?"var(--danger-ink)"
     : (alive===1?"var(--warn-ink)":"var(--success-ink)");
   tgTag.style.borderColor = alive===0?"rgba(224,92,78,.45)"
     : (alive===1?"var(--warn-line)":"rgba(86,168,120,.40)");}
 l0.appendChild(tgTag);t0.appendChild(l0);
 /* 重新探活按钮（配置层看不出 402/401，必须真发请求） */
 var pb=document.createElement("button");
 pb.className="btn";pb.textContent="重新探活";
 pb.style.cssText="height:26px;padding:0 11px;font-size:12px;margin-left:auto";
 pb.onclick=function(){seeProbe(pb);};
 t0.appendChild(pb);
 r0.appendChild(t0);
 var vd=el("div","knob__desc",vt);
 vd.style.color = alive===null?"var(--text-secondary)"
   : (alive===0?"var(--danger-ink)":(alive===1?"var(--warn-ink)":"var(--text-secondary)"));
 r0.appendChild(vd);box.appendChild(r0);

 /* ② 网页识图那一段（see.web 开关 + 问哪一家 + 通道冷却） */
 if(dg.web){
   var w=dg.web;
   var rw=el("div","knob knob--ro");
   var tw=el("div","knob__top");var lw=el("div","knob__lab");
   lw.appendChild(el("span","knob__name","网页识图（带联网那一档）"));
   var twTag=el("span","tag",w.on?"开":"关");
   twTag.style.color=w.on?"var(--success-ink)":"var(--text-muted)";
   lw.appendChild(twTag);
   if(w.on)lw.appendChild(el("span","tag",w.backend||"?"));
   tw.appendChild(lw);rw.appendChild(tw);
   var wd=el("div","knob__desc");
   wd.textContent=(w.note||"")
     + (w.on?("　—— 关掉它能省掉每张图 7~13 秒的网页等待。"):"　—— 想要「认角色/带联网」，把这个开关打开（先去下面点「登录」）。");
   rw.appendChild(wd);
   if(!w.on||!w.ready){box.appendChild(rw);}else{box.appendChild(rw);}}

 /* ③ 接力链 + 每档健康 */
 var chain=(dg.chain&&dg.chain.length)?dg.chain:(g.see_chain||[]);
 if(chain.length){
   var r=el("div","knob knob--ro");
   var top=el("div","knob__top");var lab=el("div","knob__lab");
   lab.appendChild(el("span","knob__name","识图接力顺序"));
   lab.appendChild(el("span","tag","按下单顺序试"));
   top.appendChild(lab);r.appendChild(top);
   var ol=el("div","knob__desc");ol.style.lineHeight="1.95";
   chain.forEach(function(x,i){
     var dead=(x.state==="nodead")||(x.state==="probe_dead")||x.dead;
     var local=(x.state==="local");
     var sp=el("span",null,"");
     var mark=dead?"✗ ":((i===0)?"① ":(local?"⌂ ":"· "));
     sp.textContent=mark+(i+1)+". "+x.backend+" / "+(x.model||"（该后端默认模型）");
     sp.style.color = dead?"var(--danger-ink)" : (local?"var(--warn-ink)":"var(--text-secondary)");
     ol.appendChild(sp);
     var ntText=x.note||x.why||"";
     if(ntText){
       var nt=el("span",null,"　— "+ntText);
       nt.style.color=dead?"var(--danger-ink)":"var(--text-muted)";
       ol.appendChild(nt);}
     if(i<chain.length-1)ol.appendChild(el("br"));});
   r.appendChild(ol);
   var tip=el("div","knob__desc","✗ = 这一档用不了（Key 失效 / 余额不足 / 没配 url）；"
     +"① = 首选；⌂ = 本机 Ollama（不出网，慢但不限次）。"
     +"标 ✗ 的档会被跳过，白花的只有一次请求的时间。");
   tip.style.color="var(--text-muted)";r.appendChild(tip);
   box.appendChild(r);}

 /* ④ 最近一次实际接力：谁答的、谁挂了、各花多久 */
 var last=dg.last||g.see_last||{};
 if(last.steps&&last.steps.length){
   var r2=el("div","knob knob--ro");
   var t2=el("div","knob__top");var l2=el("div","knob__lab");
   l2.appendChild(el("span","knob__name","最近一次识图"));
   var okTag=el("span","tag",last.ok?"成功":"全挂");
   okTag.style.color=last.ok?"var(--success-ink)":"var(--danger-ink)";
   l2.appendChild(okTag);t2.appendChild(l2);r2.appendChild(t2);
   var d2=el("div","knob__desc");d2.style.lineHeight="1.95";
   var head=el("span",null,"答的："+(last.via||"?")+"　("+(last.ts||"")+")");
   head.style.color=last.ok?"var(--success-ink)":"var(--danger-ink)";
   d2.appendChild(head);d2.appendChild(el("br"));
   var tot=0;
   last.steps.forEach(function(s){
     tot+=(parseFloat(s.sec)||0);
     var line=el("span",null,"");
     line.textContent=(s.ok?"✓ ":"✗ ")+s.backend+" / "+(s.model||"?")
       +"　"+s.sec+"s"+(s.err?("　— "+s.err):"");
     line.style.color=s.ok?"var(--success-ink)":"var(--text-muted)";
     d2.appendChild(line);d2.appendChild(el("br"));});
   var sm=el("span",null,"合计 "+tot.toFixed(1)+" 秒");
   sm.style.color="var(--text-muted)";d2.appendChild(sm);
   r2.appendChild(d2);box.appendChild(r2);}
 else{
   var r3=el("div","knob knob--ro");
   var d3=el("div","knob__desc","还没识过图。群友发图（或@它看图）之后，"
     +"这里会显示那次实际用了哪个模型、别的档为什么没答上、各花了几秒。");
   r3.appendChild(d3);box.appendChild(r3);}
 /* ⚠️ 本函数是**独立函数**，作用域里没有 render 的 rows（2026-10-07 踩过：
    写成 rows.appendChild(...) ⇒ ReferenceError: rows is not defined ⇒
    groups.forEach 在「识图」这组中断 ⇒ 后面所有卡片不渲染、buildCatNav() 也跑不到
    ⇒ 整页左侧分区导航空白。且 load() 的 .catch 是空块，控制台连报错都看不到）。
    这里**只返回节点**，由调用方 rows.appendChild(seeDiag(g)) 负责挂载。 */
 return foldBox("识图诊断（能不能识图 / 谁答的 / 谁挂了）",box,DESCON);}
function infoRows(list,rows){
 /* 能力说明行：标题 + 角标（有开关/自动）+ 描述，复用 knob 只读样式，不可点。
    这 5 条描述合计近 400 字，整块折起来，别把「群务」分区撑出一屏。 */
 var box=el("div","slots");
 list.forEach(function(p){
  var r=el("div","knob knob--ro");
  var top=el("div","knob__top");
  var lab=el("div","knob__lab");
  lab.appendChild(el("span","knob__name",p[0]));
  lab.appendChild(el("span","tag",p[2]||"自动"));
  top.appendChild(lab);r.appendChild(top);
  r.appendChild(el("div","knob__desc",p[1]));
  box.appendChild(r);});
 rows.appendChild(foldBox(list.length+" 项群聊能力（点开看说明）",box,DESCON));}
function brainSplit(items){
 /* 「大脑」卡只列当前大脑用得上的项：knob 上带 brain 且不是 api 的按大脑收起，
    没带的（大脑本身、备用、模型选择、思考强度）恒显。 */
 var cur="";items.forEach(function(it){if(it.key==="ai.provider")cur=it.value;});
 cur=(""+(cur||"")).toLowerCase();
 var vis=[],hid=0;
 items.forEach(function(it){
   if(it.web_hidden){hid++;return;}
   var b=it.brain;
   if(!b||b==="api"||b===cur)vis.push(it);else hid++;});
 return {vis:vis,hid:hid,cur:cur};}

/* depends_on（2026-10-30）：这一项当前该不该藏。
   后端在 KNOBS 里写 {"key":"see.follow_brain","want":false} 表示
   「see.follow_brain 不等于 want 时把本项藏起来」，前端据此过滤。
   取值来源是本次已加载的配置快照（LAST，由 load() 填）——
   开关改完会自动 POST → load() → 重画，所以这里读到的一定是最新值。 */
function depHidden(it){
 var d=it&&it.depends_on; if(!d||!d.key)return false;
 var v=curValueOf(d.key);
 if(v===undefined||v===null)return false;
 return nbBool(v)!==nbBool(d.want);
}
function nbBool(x){
 if(typeof x==="boolean")return x;
 var s=(""+(x===null||x===undefined?"":x)).trim().toLowerCase();
 if(s==="true"||s==="1"||s==="开"||s==="on"||s==="yes")return true;
 if(s==="false"||s==="0"||s==="关"||s==="off"||s==="no"||s==="")return false;
 return !!x;
}
/* 从已加载的配置快照里取某个旋钮的当前值（LAST.groups[].items[]）。 */
function curValueOf(key){
 var gs=(LAST&&LAST.groups)||[];
 for(var i=0;i<gs.length;i++){
   var its=gs[i].items||[];
   for(var j=0;j<its.length;j++){
     if(its[j]&&its[j].key===key)return its[j].value;
   }
 }
 return undefined;
}

function advFold(items){
 /* 卡尾「高级参数」折叠（2026-11-01 总控重构）：数值/文本类调参的收纳处。
    fold--adv 标记给搜索用 —— 搜索态下这些折叠要强行展开，否则搜到了也看不见。 */
 var box=el("div","");
 items.forEach(function(it){box.appendChild(row(it));});
 var d=foldBox("高级参数（"+items.length+"）",box,false);
 d.classList.add("fold--adv");
 return d;}
function row(it){
 var ro=!it.writable||it.type==="secret";
 var r=el("div","knob"+(ro?" knob--ro":"")+(!ro&&it.multiline?" knob--full":""));
 /* data-search：顶部搜索框按「名字 + key」找行（都转小写），搜索态靠它放行 */
 r.setAttribute("data-search",String((it.label||"")+" "+(it.key||"")).toLowerCase());
 var top=el("div","knob__top");
 var lab=el("div","knob__lab");
 lab.appendChild(el("span","knob__name",it.label));
 if(it.apply==="restart")lab.appendChild(el("span","tag","重启"));
 if(ro)lab.appendChild(el("span","tag","只读"));
 /* 取值范围永远是短提示，留在标签行不折叠 */
 if(it.type==="int"&&(it.min!=null||it.max!=null))
   lab.appendChild(el("span","knob__rng","["+(it.min==null?0:it.min)+"–"+(it.max==null?"∞":it.max)+"]"));
 var desc=el("div","knob__desc");
 desc.hidden=!DESCON;desc.setAttribute("data-open",DESCON?"1":"0");
 desc.textContent=it.desc||"";
 if(it.desc){
   var q=el("button","knob__q","?");q.type="button";
   q.setAttribute("aria-expanded",DESCON?"true":"false");
   q.setAttribute("aria-label","说明："+it.label);q.title="说明";
   q.onclick=function(){
     var d=desc.getAttribute("data-open")==="1";
     desc.setAttribute("data-open",d?"0":"1");desc.hidden=d;
     q.setAttribute("aria-expanded",d?"false":"true");};
   lab.appendChild(q);}
 top.appendChild(lab);
 var ctl=el("div","knob__ctl");
 var wid=control(it,ro);
 /* 🔴 2026-10-19：原来这里判 `wid.tagName==="TEXTAREA"`，但 control() 的多行分支返回的是
    包着 textarea + 字数计数的 **txtbox span**（tagName=SPAN），判据永远为假 ⇒ 走进 else
    被塞进行右侧的 .knob__ctl（flex:none）里，textarea 被压成 130px 一条竖缝贴在右边。
    现在按 class 认，包裹层也算「宽控件」。 */
 var wide=!ro&&it.multiline&&wid&&
          (wid.tagName==="TEXTAREA"||wid.classList.contains("txtbox"));
 if(wide){
   /* 多行长文本（人设正文）：不挤在行右侧的控件列里，独占一行全宽，放行标题下面 */
   r.appendChild(top);r.appendChild(wid);}
 else{ctl.appendChild(wid);top.appendChild(ctl);r.appendChild(top);}
 r.appendChild(desc);return r;}
function control(it,ro){
 if(ro)return el("span","ro",it.display);
 if(it.type==="bool"){
   var w=el("span","knob__ctl");
   w.appendChild(el("span","cst",it.value?"开":"关"));
   /* 🔴 2026-10-30：开关显示「开」但**其实用不了**（搜索 key 401 那种）。
      只报配置项等于骗人——主人会以为联网好好的，然后奇怪为什么问最新的事答不上来。
      后端 knob_items 给 `live:{usable,why,text}`，这里如实标出来。 */
   if(it.live&&it.live.usable===false&&it.value){
     var lv=el("span","cst cst--bad",it.live.why?("不可用·"+String(it.live.why).slice(0,18)):"不可用");
     lv.title="配置开着，但实际用不了："+(it.live.why||"")+"；换一个能用的 key 后自动恢复";
     w.appendChild(lv);}
   var b=el("button","switch");b.type="button";
   b.setAttribute("role","switch");b.setAttribute("aria-checked",it.value?"true":"false");
   b.setAttribute("aria-label",it.label);
   b.appendChild(el("span","switch__dot"));
   b.onclick=function(){set({key:(it.key||it.label),
     value:b.getAttribute("aria-checked")==="true"?"关":"开"});};
   w.appendChild(b);return w;}
 if(it.type==="enum"){
   var vs=(it.values||[]).slice();
   var cur=(it.value==null)?"":String(it.value);
   /* 「活跃=自定义」这种反查不出档位的值、以及取值为空时，单独补一项，别显示成没选中 */
   var extra=(cur==="")?"（空）":(vs.indexOf(cur)<0?cur:"");
   /* 2026-10-29 晚：picker = select 旁的「拉取」按钮（大脑=自定义时的「模型选择」）。
      点了走带搜索的选择窗，点选即存 —— 不进 seg 短档分支，模型只有一个入口。
      更晚：识图卡片也用它（picker_vl=只筛视觉模型、picker_prefix=加 `custom/` 前缀）——
      识图模型和聊天模型是**两套配置**，各自选、互不影响。 */
   if(it.picker){
     var pw=el("span","");
     pw.style.cssText="display:inline-flex;align-items:center;gap:6px";
     var wp=el("span","selwrap");
     var sp=el("select","field");
     /* 三个控件（下拉/拉取/测速）挤一行会互相压窄 —— 下拉给个下限宽度，
        长模型名靠省略号收尾（原值在 config 里，下拉只是选它）。 */
     sp.style.cssText="min-width:150px;max-width:210px";
     if(extra){var eo=el("option",null,extra);eo.value=cur;eo.selected=true;sp.appendChild(eo);}
     vs.forEach(function(v){var o=el("option",null,v===""?"（默认）":v);o.value=v;
       if(cur===String(v))o.selected=true;sp.appendChild(o);});
     sp.onchange=function(){set({key:(it.key||it.label),value:sp.value});};
     wp.appendChild(sp);
     var pb=el("button","btn btn--sm","拉取");pb.type="button";
     pb.title=it.picker_vl?"问自定义站有哪些【视觉】模型，弹清单点选":"问该站有哪些模型，弹清单点选";
     pb.onclick=function(){openModelPick(function(m){
       set({key:(it.key||it.label),value:(it.picker_prefix||"")+m});},
       {vl:!!it.picker_vl,title:it.picker_vl?"选择视觉模型":"选择模型"});};
     pw.appendChild(wp);pw.appendChild(pb);
     /* 2026-10-29 深夜：测速按钮（主人「给自定义模型加一个测速按钮，用这个我选的吧」）。
        测的是**当前这个框里选中的值**（读 config，不是我猜的默认档）——
        点完真跑一次流式请求，量首字/总耗时/token，顺手判「正文空/不吃图」。
        放在 picker 分支里，所以只出现在「模型选择」和「识图模型」两张卡上
        （它们才是真能换模型的；别的 enum 没这个按钮，也不该有）。 */
     var tb=el("button","btn btn--sm","测速");tb.type="button";
     tb.title="真跑一次这个模型：首字延迟 / 总耗时 / token / 会不会白吐";
     tb.onclick=function(){probeModel(it,tb);};
     pw.appendChild(tb);
     return pw;}
   if(vs.length&&vs.length+(extra?1:0)<=5&&(vs.join("")+extra).length<=26){
     var seg=el("div","seg");
     if(extra){
       var cb=el("button","seg__b seg__b--cur",extra);cb.type="button";cb.disabled=true;
       cb.setAttribute("aria-pressed","true");cb.title="当前值不在预选项里，点别的档位会覆盖它";
       seg.appendChild(cb);}
     vs.forEach(function(v){
       var b=el("button","seg__b",v);b.type="button";
       b.setAttribute("aria-pressed",cur===String(v)?"true":"false");
       b.onclick=function(){set({key:(it.key||it.label),value:v});};
       seg.appendChild(b);});
     return seg;}
   var wrap=el("span","selwrap");
   var s=el("select","field");
   if(extra){var eo=el("option",null,extra);eo.value=cur;eo.selected=true;s.appendChild(eo);}
   vs.forEach(function(v){var o=el("option",null,v===""?"（默认）":v);o.value=v;
     if(cur===String(v))o.selected=true;s.appendChild(o);});
   s.onchange=function(){set({key:(it.key||it.label),value:s.value});};
   wrap.appendChild(s);return wrap;}
 if(it.type==="int"){
   var n=document.createElement("input");n.type="number";n.className="field num";
   n.value=(it.value==null?"":it.value);
   if(it.min!=null)n.min=it.min;if(it.max!=null)n.max=it.max;
   n.onchange=function(){set({key:(it.key||it.label),value:n.value});};
   return n;}
 if(it.type==="str"&&it.multiline){
   var box=el("span","txtbox");
   box.style.cssText="display:block;width:100%";
   var ta=document.createElement("textarea");ta.className="field txtarea";ta.rows=(it.rows||7);
   ta.style.marginTop="8px";
   ta.value=(it.value==null?"":it.value);
   var cnt=el("span","txtcnt");
   cnt.style.cssText="display:block;font-size:11px;opacity:.55;text-align:right;margin-top:4px";
   function _up(){cnt.textContent=(ta.value||"").length+" / "+(it.max_len||"—");}
   ta.oninput=_up; ta.onkeyup=_up; _up();
   ta.onchange=function(){set({key:(it.key||it.label),value:ta.value});};
   box.appendChild(ta);box.appendChild(cnt);
   return box;}
 var t=document.createElement("input");t.type="text";t.className="field txt";
 t.value=(it.value==null?"":it.value);
 /* 2026-10-29：密钥类字段（mask）永不回显明文 —— 密码框 + 「留空=不改」提示 */
 if(it.mask){t.type="password";t.autocomplete="new-password";
   t.placeholder=(it.display==="已配置"?"已配置（留空=不改）":"未配置，填上密钥");}
 t.onchange=function(){if(it.mask&&!t.value)return;   /* 留空=不改，别提交空值 */
   set({key:(it.key||it.label),value:t.value});};
 /* 2026-10-29 晚：「自定义API 模型」旋钮已删，模型统一走「模型选择」（picker 在 enum 分支），
    str 控件不再需要拉取按钮。 */
 return t;}
/* ── 模型选择窗（2026-10-29）：带搜索框的居中小窗，点条目回调 onPick，Esc/点遮罩关。
      由「模型选择」旁的「拉取」按钮触发（大脑=自定义时），点了才请求 /api/custom_models。 */
var MPICK=null;
function closeModelPick(){
 if(MPICK){[MPICK.scrim,MPICK.panel].forEach(function(n){
   if(n&&n.parentNode)n.parentNode.removeChild(n);});}
 MPICK=null;
 document.removeEventListener("keydown",mpickEsc);}
function mpickEsc(e){if(e.key==="Escape"||e.keyCode===27)closeModelPick();}
function openModelPick(onPick,opt){
 closeModelPick();
 opt=opt||{};
 var scrim=el("div","scrim");scrim.onclick=closeModelPick;
 var panel=el("div","mpick");panel.setAttribute("role","dialog");
 panel.setAttribute("aria-label",opt.title||"选择模型");
 var head=el("div","mpick__head");
 head.appendChild(el("div","mpick__title",opt.title||"选择模型"));
 var xb=el("button","btn btn--sm","关闭");xb.type="button";xb.onclick=closeModelPick;
 head.appendChild(xb);
 var body=el("div","mpick__body");
 var si=document.createElement("input");si.type="text";
 si.className="field txt mpick__search";si.placeholder="输入关键字过滤…";
 var list=el("div","mpick__list");
 var cnt=el("div","mpick__count","拉取中…");
 body.appendChild(si);body.appendChild(list);body.appendChild(cnt);
 panel.appendChild(head);panel.appendChild(body);
 document.body.appendChild(scrim);document.body.appendChild(panel);
 MPICK={scrim:scrim,panel:panel};
 document.addEventListener("keydown",mpickEsc);
 var ALL=[];
 function render(){
   var q=(si.value||"").trim().toLowerCase();
   var vs=q?ALL.filter(function(m){return m.toLowerCase().indexOf(q)>=0;}):ALL;
   list.innerHTML="";
   if(!vs.length){
     var tip;
     if(ALL.length)tip=q?"没匹配的，换个关键字":"清单是空的";
     else tip=opt.vl?"该站没拉到视觉模型（可以先不加筛选拉一次看看）":"没拉到模型";
     list.appendChild(el("div","mpick__empty",tip));
   }else{
     vs.forEach(function(m){
       var b=el("button","mpick__item",m);b.type="button";
       b.onclick=function(){onPick(m);closeModelPick();};
       list.appendChild(b);});}
   cnt.textContent=ALL.length?("共 "+ALL.length+" 个"+(q?"，匹配 "+vs.length+" 个":"")):"";}
 si.oninput=render;
 fetch("/api/custom_models"+(opt.vl?"?vl=1":""),{headers:{"X-Bridge-Token":TOKEN}})
 .then(function(r){return r.json();})
 .then(function(d){
   if(!MPICK)return;                      /* 窗已经关了就别画 */
   if(d&&d.ok){ALL=d.models||[];render();}
   else{cnt.textContent=(d&&d.error)||"拉取失败";list.innerHTML="";}})
 .catch(function(e){if(MPICK)cnt.textContent="请求失败："+e;});}

/* ── 模型测速窗（2026-10-29 深夜）：主人「给自定义模型加一个测速按钮，用这个我选的吧」。
      测的是**当前卡片里选中的那个值**（it.value 原样传给后端读 config，不猜默认档）。
      结果里把「首字 / 总耗时 / token / 思考token」分开列 —— 只看总耗时会误判
      「模型慢」，其实是思考段吃光了 token（实测 raccoon/…-1017 就是这样）。 */
var MPROBE=null;
function closeProbe(){
 if(MPROBE){[MPROBE.scrim,MPROBE.panel].forEach(function(n){
   if(n&&n.parentNode)n.parentNode.removeChild(n);});}
 MPROBE=null;
 document.removeEventListener("keydown",mprobeEsc);}
function mprobeEsc(e){if(e.key==="Escape"||e.keyCode===27)closeProbe();}
function probeModel(it,btn){
 closeProbe();
 var key=it.key||it.label;
 var scrim=el("div","scrim");scrim.onclick=closeProbe;
 var panel=el("div","mpick probe");panel.setAttribute("role","dialog");
 panel.setAttribute("aria-label","模型测速");
 var head=el("div","mpick__head");
 head.appendChild(el("div","mpick__title","测速："+(it.value||"（未选）")));
 var xb=el("button","btn btn--sm","关闭");xb.type="button";xb.onclick=closeProbe;
 head.appendChild(xb);
 var body=el("div","mpick__body");
 var out=el("div","probe__out");
 var line0=el("div","probe__wait","真跑一次这个模型（最多 75 秒）…");
 body.appendChild(line0);body.appendChild(out);
 panel.appendChild(head);panel.appendChild(body);
 document.body.appendChild(scrim);document.body.appendChild(panel);
 MPROBE={scrim:scrim,panel:panel};
 document.addEventListener("keydown",mprobeEsc);
 if(btn){btn.disabled=true;}
 var q="/api/probe_model?key="+encodeURIComponent(key);
 if(!key||key.indexOf(".")<0){                       /* 兜底：显式传值 */
   q="/api/probe_model?provider="+encodeURIComponent(String(it.brain||""))+
     "&model="+encodeURIComponent(String(it.value||""));}
 fetch(q,{headers:{"X-Bridge-Token":TOKEN}})
 .then(function(r){return r.json();})
 .then(function(d){if(btn)btn.disabled=false;
   if(!MPROBE)return;
   line0.parentNode.removeChild(line0);
   renderProbe(out,d);})
 .catch(function(e){if(btn)btn.disabled=false;
   if(!MPROBE)return;line0.parentNode.removeChild(line0);
   out.appendChild(el("div","probe__bad","请求失败："+e));});}

function fmtTok(v){return (v==null)?"—":String(v);}
function renderProbe(out,d){
 out.innerHTML="";
 function row(k,v,cls){
   var r=el("div","probe__row");
   r.appendChild(el("span","probe__k",k));
   r.appendChild(el("span","probe__v"+(cls?" "+cls:""),v));
   out.appendChild(r);return r;}
 if(!d||!d.ok){
   out.appendChild(el("div","probe__bad",(d&&(d.error||d.verdict))||"没测成"));
   if(d&&d.where)row("目标",d.where);
   return;}
 var c=d.chat||{};
 row("模型",(d.provider||"?")+"/"+(d.model||"?"));
 if(d.think)row("思考强度",d.think);
 row("首字延迟",c.first!=null?(c.first+" s"):"没吐字","warn");
 row("总耗时",c.sec!=null?(c.sec+" s"):"—");
 row("输出 token",fmtTok(c.ct)+"（其中思考 "+fmtTok(c.think_tok)+"）",
   (c.think_tok&&c.ct&&c.think_tok>=c.ct*0.9)?"warn":"");
 row("输入 token",fmtTok(c.pt));
 if(c.text)row("它答的",c.text.slice(0,120));
 if(d.vision){
   var v=d.vision;
   if(v.error){row("看图","报错："+v.error.slice(0,80),"bad");}
   else{
     row("看图耗时",(v.sec!=null?v.sec+" s":"—")+"（首字 "+(v.first!=null?v.first+" s":"—")+")");
     var okAll=(d.rect_ok&&d.circ_ok);
     row("看图答的",(v.text||"（空）").slice(0,100),okAll?"good":"bad");
     row("两色判定",(d.rect_ok?"长方形✅":"长方形❌")+"　"+(d.circ_ok?"圆✅":"圆❌"),
       okAll?"good":"bad");}}
 var vb=el("div","probe__verdict "+(d.ok?"ok":"bad"),d.verdict||"");
 out.appendChild(vb);
 if(d.advice)out.appendChild(el("div","probe__advice","建议："+d.advice));}
function set(body){
 fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json",
   "X-Bridge-Token":TOKEN,"X-QQ-Source":"web"},body:JSON.stringify(body)})
 .then(function(r){return r.json();}).then(function(d){
   if(d&&d.ok){say(d.message||"改好了",true);load();}
   else{say((d&&(d.message||d.error))||"没改成",false);}})
 .catch(function(e){say("请求失败："+e,false);});}
load();
try{DESCON=localStorage.getItem("qqbot_desc")==="1";}catch(e){}
$("dsw").onclick=function(){setDesc(!DESCON);};
/* 顶部搜索：输入即过滤（防抖 120ms），Esc 清空回到原分区 */
var sbx=$("searchbox");
if(sbx){var _st=null;
  sbx.addEventListener("input",function(){clearTimeout(_st);_st=setTimeout(applySearch,120);});
  sbx.addEventListener("keydown",function(e){
    if(e.key==="Escape"){clearTimeout(_st);clearSearch();}});}
setInterval(function(){if(TOKEN&&LAST)status();},20000);
/* 视口变化要重排 span：去抖 180ms 后用已取到的数据重算（只切 hidden/span，不重新请求接口）。
   这里不重画 DOM —— 重画会清掉别处没保存的输入。 */
var _rz=null;
window.addEventListener("resize",function(){
 clearTimeout(_rz);
 _rz=setTimeout(function(){if(TOKEN&&LAST)applyCat();
   /* 窗口一变，可滚高度就变了，渐隐状态要重算（否则滚到底了还留着底部渐隐） */
   var ns=document.querySelectorAll(".chatlog,.wlmembers");
   for(var i=0;i<ns.length;i++)syncSc(ns[i]);},180);});
</script></body></html>
"""
