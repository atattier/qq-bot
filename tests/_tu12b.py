# -*- coding: utf-8 -*-
"""U12b 常驻守卫：桥接宿主全局契约（SAFE，全打桩）。

固定基线 `880211a`（U12b 之前最后一个 commit），证明：
  ① 67 个函数 / 45 个全局从 bot.X 仍可取，且 qbot_draw.X 是同一对象；
  ② 宿主 monkeypatch（bot.draw_submit / bot._draw_q / bot.CFG / bot.comfy_gen）
     对 qbot_draw 内部互调实时可见 —— 22 处旧 bot.X 打桩不失效；
  ③ qbot_draw 不 import bot；测试态 draw_time.json / subjects.json 全落临时目录；
  ④ inspect.getsource(bot.draw_submit) 仍取到现画域源码；
  ⑤ 旧路由/工具层的函数形状与行为已由专项回归覆盖，不再用整块源码逐字节锁
     把已删除的决策包装和固定话术钉死在仓库里。
"""
import ast
import inspect
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

BAD = []
BASELINE = "880211a"
if subprocess.call(["git", "cat-file", "-e", BASELINE + ":bot.py"], cwd=_ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
    print("SKIP: 当前仓库没有历史基线 %s（公开快照为单提交）" % BASELINE)
    sys.exit(0)


def chk(name, ok, detail=""):
    print(("  [OK] " if ok else "  [!!] ") + name + (("  " + str(detail)) if detail and not ok else ""))
    if not ok:
        BAD.append(name)


def _git_show(rev, path):
    return subprocess.check_output(["git", "show", "%s:%s" % (rev, path)],
                                   cwd=_ROOT, text=True, encoding="utf-8")


print("== 1 基线函数与类形状 ==")
old_src = _git_show(BASELINE, "bot.py")
new_src = (_ROOT / "bot.py").read_text(encoding="utf-8")
body = (_ROOT / "qbot_draw_body.py").read_text(encoding="utf-8")
ot, nt = ast.parse(old_src), ast.parse(new_src)
# 基线 67 函数里，2026-11-06 决策层收口**有意删除**的二次判定层不算「丢失」：
# _ai_route_draw/_route_json/_route_prompt_grounded（旧 AI 画图路由，收归主脑）。
RETIRED = {"_ai_route_draw", "_route_json", "_route_prompt_grounded",
           "_route_ai_draw", "_route_moss_edit_pic"}
old_draw = {n.name for n in ot.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and 8671 <= n.lineno <= 10891} - RETIRED
old_funcs = {n.name: n for n in ot.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name not in old_draw}
new_funcs = {n.name: n for n in nt.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for _n in ("_shadow_tools", "_shadow_model", "_shadow_addressed",
           "_shadow_history_snapshot"):
    chk("M1 函数存在：%s" % _n, _n in new_funcs)
for _n in ("_seg_gid", "_seg_hard_rules", "_seg_session_policy",
           "_shadow_context_providers", "_shadow_context_mode",
           "_shadow_assembler"):
    chk("M2 函数存在：%s" % _n, _n in new_funcs)
for _n in ("_arch_tools_mode", "_tool_registry", "_tool_principal",
           "_tool_shadow_offer", "_tool_shadow_call"):
    chk("M3 函数存在：%s" % _n, _n in new_funcs)
for cname in ("ToolsUnsupported", "Handler"):
    a = next((n for n in ot.body if isinstance(n, ast.ClassDef) and n.name == cname), None)
    b = next((n for n in nt.body if isinstance(n, ast.ClassDef) and n.name == cname), None)
    chk("%s 类 AST 全等" % cname, a is not None and b is not None
        and ast.dump(a) == ast.dump(b))
# 留在外面的业务块：U12e 的 46 个 action_* 签名和 U12a 的路由函数不许丢
old_actions = {n.name: ast.dump(n.args) for n in ot.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("action_")}
new_actions = {n.name: ast.dump(n.args) for n in nt.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("action_")}
chk("46 个 action_* 仍在 bot.py", len(old_actions) == 46
    and all(n in new_actions for n in old_actions),
    (len(old_actions), len(new_actions)))
# U12a 的消息分发路由（_route_xxx）；画图域里两个同名前缀的 LLM 判定助手
# （_route_json / _route_prompt_grounded）随 U12b 搬家，2026-11-06 又随决策层
# 收口有意删除（见 RETIRED）；旧 _route_ai_draw / _route_moss_edit_pic 同批删。
old_routes = [n.name for n in ot.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name.startswith("_route_") and n.name not in old_draw
              and n.name not in RETIRED]
chk("U12a 的消息路由 _route_* 全在 bot.py",
    all(n in new_funcs for n in old_routes), [n for n in old_routes if n not in new_funcs])

print("== 2 桥接字典运行时契约 ==")
import bot              # noqa: E402
import qbot_draw        # noqa: E402

chk("bot.qbot_draw 是同一模块", bot.qbot_draw is qbot_draw)
_qd_ast = ast.parse((_ROOT / "qbot_draw.py").read_text(encoding="utf-8"))
_imports = [a.name for n in _qd_ast.body if isinstance(n, ast.Import) for a in n.names]
_imports += [n.module or "" for n in _qd_ast.body if isinstance(n, ast.ImportFrom)]
chk("qbot_draw 不 import bot（AST 判定，注释不算）",
    "bot" not in _imports, _imports)
chk("67 个函数 + 45 个全局都还在 bot.X",
    all(hasattr(bot, n) for n in old_draw), [n for n in old_draw if not hasattr(bot, n)][:8])
chk("bot 与 qbot_draw 指向同一批函数对象",
    all(getattr(bot, n) is getattr(qbot_draw, n, object()) for n in old_draw))
chk("qbot_draw 函数 __globals__ 是桥接字典",
    isinstance(qbot_draw.draw_submit.__globals__, qbot_draw._BridgeGlobals))
chk("CFG 是宿主活字典（不是副本）",
    qbot_draw._comfy_cfg.__globals__["CFG"] is bot.CFG)
# 宿主桩必须被内部互调看见，不是只骗过入口
_orig_submit = bot.draw_submit
bot.draw_submit = lambda *a, **k: "U12B_STUB"
chk("bot.draw_submit 桩对 qbot_draw 内部互调可见",
    qbot_draw.parse_draw.__globals__["draw_submit"]() == "U12B_STUB")
bot.draw_submit = _orig_submit
_orig_q = bot._draw_q
bot._draw_q = "U12B_QUEUE_STUB"
chk("bot._draw_q 替换对象对 draw_submit 可见",
    qbot_draw.draw_submit.__globals__["_draw_q"] == "U12B_QUEUE_STUB")
bot._draw_q = _orig_q
chk("bot._draw_switch 宿主值对 comfy_enabled 可见",
    qbot_draw.comfy_enabled.__globals__["_draw_switch"] is bot._draw_switch)
# 运行期 global 写入仍回写宿主：_subject_book_inst / _draw_switch 都不另立山头
chk("模块显式 global 写入清单完整",
    qbot_draw._HOST_WRITES == {"_draw_switch", "_subject_book_inst"},
    qbot_draw._HOST_WRITES)
# KNOBS 里是函数对象，不许变成断掉的旧别名
_knobs = [v for entry in bot.KNOBS for v in entry.values() if callable(v)]
chk("KNOBS 仍持有 _moss_studio_status 函数对象",
    any(getattr(f, "__name__", "") == "_moss_studio_status" for f in _knobs))

print("== 3 源码级 inspect 与测试态隔离 ==")
for name in ("draw_submit", "parse_draw", "do_draw", "draw_worker", "handle_comfy_cmd"):
    chk("inspect.getsource(bot.%s) 仍在原源码" % name,
        inspect.getsource(getattr(bot, name)) in body)
chk("bot.py 有 U12b install 接线",
    "qbot_draw.install(globals()" in new_src)
chk("bot.py 测试态有 qbot_draw.bind",
    "qbot_draw.bind(state_dir=_td)" in new_src)
# ruff F821 看不见 install 注入的名字；bot.py 的显式 from qbot_draw import
# 必须与随迁源码定义的 112 个名字逐一对齐，漏一个就会在静态扫描里假阴性。
_body_names = set()
for _n in ast.parse(body).body:
    if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _body_names.add(_n.name)
    elif isinstance(_n, ast.Assign):
        for _t in _n.targets:
            if isinstance(_t, ast.Name):
                _body_names.add(_t.id)
_imports = [n for n in nt.body if isinstance(n, ast.ImportFrom) and n.module == "qbot_draw"]
chk("bot.py 的 qbot_draw 显式登记与 112 个随迁符号全等",
    len(_imports) == 1 and {a.name for a in _imports[0].names} == _body_names,
    (len(_body_names), len(_imports[0].names) if _imports else 0))
_tmp = Path(tempfile.gettempdir()).resolve()
def _in_tmp(p):
    try:
        return Path(str(p)).resolve().is_relative_to(_tmp)
    except Exception:
        return False
chk("bot.DRAW_TIME_FILE 在临时目录", _in_tmp(bot.DRAW_TIME_FILE), bot.DRAW_TIME_FILE)
chk("qbot_draw.DRAW_TIME_FILE 在临时目录", _in_tmp(qbot_draw.DRAW_TIME_FILE),
    qbot_draw.DRAW_TIME_FILE)
chk("桥接字典 STATE 在临时目录",
    _in_tmp(qbot_draw._subject_book.__globals__["STATE"]),
    qbot_draw._subject_book.__globals__["STATE"])
chk("桥接字典 DRAW_TIME_FILE 在临时目录",
    _in_tmp(qbot_draw._est_learn.__globals__["DRAW_TIME_FILE"]),
    qbot_draw._est_learn.__globals__["DRAW_TIME_FILE"])
chk("_tisolation 已登记 qbot_draw",
    '"qbot_draw"' in (_ROOT / "tests" / "_tisolation.py").read_text(encoding="utf-8"))
chk("_tstateiso 已保护 state/draw_time.json",
    "state/draw_time.json" in (_ROOT / "tests" / "_tstateiso.py").read_text(encoding="utf-8"))
# 真写一次，证明 _est_learn 确实只写临时副本
_orig_save = bot.save_config
bot.save_config = lambda: True
try:
    qbot_draw._est_learn("U12b_guard", 42)
finally:
    bot.save_config = _orig_save
_p = qbot_draw._est_learn.__globals__["DRAW_TIME_FILE"]
chk("_est_learn 实写临时样本文件", _in_tmp(_p) and _p.exists()
    and "U12b_guard" in _p.read_text(encoding="utf-8"), _p)

print()
print("RESULT:", "PASS" if not BAD else "FAIL (%d): %s" % (len(BAD), ", ".join(BAD)))
sys.exit(1 if BAD else 0)
