# -*- coding: utf-8 -*-
"""DSH 大脑离线测试：桩掉子进程，不真跑 DSH、不联网、不花钱。
用法： python tests/_tdsh.py
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

ok = True


def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


import bot  # noqa: E402

print("== 默认配置兜底 ==")
# 先验「代码里的默认值」：主人把 DSH 关掉是正常设置，不该让它把用例弄红
_saved_dsh = bot.CFG.pop("dsh", None)
c = bot.dsh_cfg()
chk("enabled 默认 True", c.get("enabled") is True)
chk("默认 provider=nvidia", c.get("provider") == "nvidia", c.get("provider"))
chk("默认 model", c.get("model") == "z-ai/glm-5.3-flash", c.get("model"))
chk("默认 only_owner=True", c.get("only_owner") is True)
_cli = c.get("cli") or ""
if _cli and os.path.exists(_cli):
    chk("cli 存在", True, _cli)
else:
    print("  SKIP cli 存在（本机未安装 DSH CLI：%s，用 QBOT_DSH_CLI 指定）" % _cli)

print("== 大脑候选里有 dsh ==")
chk("_provider_names 含 dsh", "dsh" in bot._provider_names(), bot._provider_names())

if _saved_dsh is not None:
    bot.CFG["dsh"] = _saved_dsh
    if _saved_dsh.get("enabled") is False:
        print("  [--] 注意：config 里 dsh.enabled=False，下面「真选中」相关用例按关闭态验")

print("== 选中判定 ==")
old_prov = bot.CFG.get("ai", {}).get("provider")
bot.CFG.setdefault("ai", {})["provider"] = "dsh"
chk("provider=dsh → dsh_selected()", bot.dsh_selected())
bot.CFG["ai"]["provider"] = "deepseek"
chk("provider=deepseek → 不选中", not bot.dsh_selected())
bot.CFG["ai"]["provider"] = old_prov

print("== 模型候选随 provider 变 ==")
d = bot.dsh_cfg()
old_p = d.get("provider")
d["provider"] = "nvidia"
chk("nvidia 有 glm-5.3-flash", "z-ai/glm-5.3-flash" in bot._dsh_model_names(), bot._dsh_model_names())
d["provider"] = old_p

print("== patch 生成 ==")
p = bot._dsh_patch_file()
chk("生成了 patch 文件", bool(p) and os.path.exists(p), p)
txt = Path(p).read_text(encoding="utf-8") if p else ""
for key in ("llm-pi-ai", "nvidia:", "NVIDIA_API_KEY", "integrate.api.nvidia.com",
            "agent-default-model", "reasoningEfforts", "reasoningEffort:"):
    chk("patch 含 %s" % key, key in txt)

print("== prompt 组装 ==")
pr = bot.dsh_prompt(123450001, "帮我数一下下载目录有多少文件", who="汪同学")
chk("含人设标记", "机器人的人设" in pr)
chk("含用户原话", "帮我数一下下载目录有多少文件" in pr)
chk("含'原样发到群里'约束", "原样发到群里" in pr)

print("== 权限闸门 ==")
bot.dsh_cfg()["only_owner"] = True
# ⚠️ 闸门实际判的是 is_master()→master_qqs()，不是 owner_qq()。以前拿 owner_qq 的
#    set 首元素来试 —— 多主人名单时 set 顺序随进程变，会随机挂（2026-11-04 抓到）。
masters = list(bot.master_qqs())
if masters:
    chk("主人放行", bot._dsh_allowed(123450001, masters[0]), masters)
else:
    print("  (skip) master_qqs 为空，跳过放行用例")
chk("陌生人拒绝", not bot._dsh_allowed(123450001, "999999999"))
bot.dsh_cfg()["only_owner"] = False
chk("关掉闸门后放行", bot._dsh_allowed(123450001, "999999999"))
bot.dsh_cfg()["only_owner"] = True


class _FakeP:
    returncode = 0

    def __init__(self, out):
        self._out = out

    def communicate(self, timeout=None):
        return self._out, ""

    def poll(self):
        return 0


_captured = {}


def _fake_ok(argv, **kw):
    _captured["argv"] = argv
    out = "\n".join([
        json.dumps({"type": "session", "sessionId": "session-test-1"}),
        json.dumps({"type": "thinking", "text": "ignore me"}),
        json.dumps({"type": "text", "text": "部分"}),
        json.dumps({"type": "final", "text": "一共有 42 个文件"}),
    ])
    return _FakeP(out)


print("== run_dsh 解析（桩掉子进程）==")
orig = bot.subprocess.Popen
bot.subprocess.Popen = _fake_ok
try:
    done, text, sent = bot.run_dsh(123450001, "数文件")
    chk("done=True", done is True)
    chk("取到 final 文本（不是 text/thinking）", text == "一共有 42 个文件", text)
    chk("sent=False", sent is False)
    argv = _captured.get("argv") or []
    chk("argv 带 --json", "--json" in argv, argv[:4])
    chk("argv 带 --patch", "--patch" in argv)
    chk("argv 带 --profile headless", "--profile" in argv and "headless" in argv)
    chk("session 落库", bot.dsh_session("g123450001") == "session-test-1",
        bot.dsh_session("g123450001"))
finally:
    bot.subprocess.Popen = orig

print("== 坏输出容错 ==")
bot.subprocess.Popen = lambda argv, **kw: _FakeP("这不是JSON\n啥也没有")
try:
    done, text, sent = bot.run_dsh(123450001, "x")
    chk("解析不到 final 时优雅失败", done is False and text == "", (done, text))
finally:
    bot.subprocess.Popen = orig

print("== CLI 不存在时降级 ==")
saved_cli = bot.dsh_cfg().get("cli")
bot.dsh_cfg()["cli"] = "Z:/nonexistent/dsh.cmd"
chk("dsh_enabled()=False", not bot.dsh_enabled())
bot.dsh_cfg()["cli"] = saved_cli

print("\n==== " + ("ALL PASS" if ok else "有用例失败") + " ====")
sys.exit(0 if ok else 1)
