# -*- coding: utf-8 -*-
"""决策层收口：旧路由不再二次判定；工具层不再固定发言。

这是删除式回归：断言被用户明确拒绝的外包决策与点名固定话术不存在，
同时断言已有主脑链路和协议/权限/资源硬轨仍在。不联网、不发消息。
"""
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

BAD = []


def chk(name, ok, why=""):
    if not ok:
        BAD.append(name)
    print(("  [OK] " if ok else "  [失败] ") + name + ("" if ok else " | " + why))


BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
DRAW = (ROOT / "qbot_draw_body.py").read_text(encoding="utf-8")
PY = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
               for p in sorted(ROOT.glob("*.py")))

# 单一主脑决策：M4 在旧路由前另跑一次 chat()、用 TLS 关旧路径的整个包装删掉。
for token in ("_route_draw_core(", "_core_draw_chat", "_CORE_TLS",
              "qbot_draw_intent", "QBOT_ARCH_DRAW", "compose_refs"):
    chk("已删除重复决策层 " + token, token not in BOT)

# 第二刀（2026-11-06）：旧路由的正则/二次模型意图判定删除，决策收归主脑。
for token in ("_route_ai_draw", "_ai_route_draw", "_ROUTE_SYS",
              "_route_json", "_route_prompt_grounded",
              "_route_moss_edit_pic", "_looks_like_edit", "_moss_only",
              "maybe_send_meme", "MEME_ASK_RE"):
    chk("已删除正则意图判定 " + token, token not in BOT and token not in DRAW)

# 已有生产主脑决策链、自然语言出口、协议与权限/资源硬轨保留。
for token in ("def _route_brain_reply(", "def ai_reply(", "def brain_with_tools(",
              "def _route_admin_slash(", "def _omni_tools(", "def _exec_bot_tool(",
              "def send_bubbles(", "def handle_admin_cmd(", "def handle_command(",
              "def _route_ban_words("):
    chk("保留硬轨/主脑出口 " + token, token in BOT)
chk("_handle_message 仍交给主脑", "_route_brain_reply(gid, uid, text" in BOT)

# 用户点名的机器人话术与出图/改图链路的受理、进度、完成、失败模板全部删除。
PHRASES = (
    "画好了（", "照着参考图反推了 %d 个框", "照着参考图改，走云端 Moss",
    "收到，先看清这张图的布局", "收到，先把这张图反推成布局提示词",
    "照那条 Moss 提示词重画", "走云端 Moss，大概", "现成 Moss 提示词直出",
    "布局没成，改走英文翻译", "收到，走云端 Moss 画好后发这里",
    "已经在弄了，好了就发出来", "这件我已经动手了，弄好就发出来",
    "想问什么？把问题跟在后面发给我", "画什么？把描述发我",
    "脑子有点掉线（", "图画好了但没发出去：", "画不出来：",
    "想画什么？把画面描述发我就行", "画「%s」", "要说改哪儿呀",
    "这张图我这边没收到", "描述太长了，画图描述最多",
    "已排队 #%d", "已按参考图开工", "反推已开工，结果会直接发出来",
    "视频任务已交队列（很慢）", "图渲染好会自动发到这边",
    "画着呢，大概%d秒", "收到，反推中…", "—— Moss 布局提示词",
    "—— danbooru 标签", "—— 英文提示词", "收到，在反推这张图的视频提示词",
    "先发一张图片，再说「反推视频提示词」", "这几张参考图我这边没拼起来",
)
for phrase in PHRASES:
    chk("已删除固定话术 " + phrase, phrase not in PY)

# 工具执行层不得直接说人话；函数体源码级检查，避免误伤管理命令与协议回执。
def fn_src(source, name):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return ""


DRAW_TOOL_FNS = ("draw_submit", "do_draw", "send_drawn", "_moss_prompt")
for name in DRAW_TOOL_FNS:
    src = fn_src(DRAW, name)
    chk(name + " 无 send_group", "send_group(" not in src)
    chk(name + " 无 ack_text 分支", "ack_text" not in src)
    chk(name + " 无 caption 模板", "caption =" not in src and "caption +=" not in src)

BOT_TOOL_FNS = (
    "handle_moss_draw", "handle_moss_edit", "handle_moss_reverse",
    "_route_bare_draw_and_submit",
    "_route_brain_reply", "brain_with_tools", "ai_reply",
    "handle_voice_cmd", "voice_worker", "send_latest_comfy_image",
    "handle_random_draw", "handle_reverse", "_rev_worker", "handle_i2i",
    "_i2i_worker", "_cover_send_voice", "_do_cover", "cover_worker",
    "_cover_from_video", "handle_cover_cmd", "handle_cover_reply",
    "handle_status_card", "handle_poke_cmd", "handle_video_link", "_do_video",
    "_jm_upload", "_jm_send_file", "handle_jm_cmd", "_do_jm",
    "_do_caption_gen", "handle_video_gen", "_do_video_gen",
    "_route_video_cmds", "action_moss_submit", "action_generate",
)
# 这些函数只保留纯产物发送；显式命令/权限硬轨由专门的协议函数承担。
SINK_OK = ("handle_voice_cmd", "handle_cover_cmd", "handle_video_gen",
           "_route_video_cmds", "handle_jm_cmd", "_cover_send_voice")
for name in BOT_TOOL_FNS:
    src = fn_src(BOT, name)
    chk(name + " 函数存在", bool(src.strip()), "bot.py 里没有这个函数名，检查是不是改名了")
    if name not in SINK_OK:
        chk(name + " 无直接 send_group", "send_group(" not in src)
    chk(name + " 无直接 send_reply", "send_reply(" not in src)
    chk(name + " 无 ack_text", "ack_text=" not in src)

for name in ("handle_comfy_cmd", "handle_engine_switch",
             "handle_comfy_engine", "handle_comfy_style"):
    chk(name + " 仍为显式协议回执", "send_group(" in fn_src(DRAW, name))

chk("删除 _omni_fallback_say", "def _omni_fallback_say(" not in BOT)
chk("已删除 M4 意图模块", not (ROOT / "qbot_draw_intent.py").exists())

print("结果：%s" % ("全部通过" if not BAD else "失败 %d 项：%s" % (len(BAD), ", ".join(BAD))))
sys.exit(1 if BAD else 0)
