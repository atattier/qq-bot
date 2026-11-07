# -*- coding: utf-8 -*-
"""U12e 常驻回归：HTTP 路由表化的等价性守卫（2026-11-05）。

只验「路由层契约」，不联网、不碰生产 config.json：
  ① 旧 if/elif 链的全部前缀 + 顺序 == 当前保序注册表（AST 比对固定基线 701efe6 版 bot.py；
     不能用 HEAD，否则 U12e 自己提交后基线会跟着漂移）
  ② 每张表 method 独立、条目数固定、无重复前缀
  ③ 顺序陷阱：窄前缀必须排在宽前缀之前，否则会串路由
  ④ 统一 dispatcher 仍在两个方法里；POST 的 4 条源码级安全原语仍原位
  ⑤ 4 个 raw handler 仍自己写响应，dispatcher 不得二次包 200

这些断言证明「路由选择和 HTTP 响应包装没被改写」；不证明各 action_* 的业务结果，
也不证明真 NapCat 收发——那些由 _thttp/_tconsole/_tnotify/_ttestmute 与真链路冒烟覆盖。
"""
import ast
import io
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

FAIL = []


def chk(label, cond, extra=""):
    print("   %s %s%s" % ("[OK]" if cond else "[失败]", label,
                         (" | " + str(extra)[:200]) if extra else ""))
    if not cond:
        FAIL.append(label)


BASELINE = "701efe6"   # U12e 之前的最后一个 commit；固定基线，不随 HEAD 漂移
if subprocess.call(["git", "cat-file", "-e", BASELINE + ":bot.py"], cwd=str(ROOT),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
    print("SKIP: 当前仓库没有历史基线 %s（公开快照为单提交）" % BASELINE)
    sys.exit(0)


def old_routes(method):
    """从基线版 bot.py 的 do_GET / do_POST 里抽原 if/elif 链的前缀顺序。"""
    try:
        src = subprocess.check_output(["git", "show", BASELINE + ":bot.py"],
                                      cwd=str(ROOT)).decode("utf-8")
    except Exception as e:
        return None, "读 HEAD:bot.py 失败: %s" % e
    tree = ast.parse(src)
    fn = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == method]
    if not fn:
        return None, "HEAD 版找不到 %s" % method

    def api_if(node):
        """找到方法里 `if/elif <path>.startswith("/api/")` 那道总门。"""
        for n in ast.walk(node):
            if isinstance(n, ast.If):
                t = n.test
                if (isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute)
                        and t.func.attr == "startswith" and t.args
                        and isinstance(t.args[0], ast.Constant)
                        and t.args[0].value == "/api/"):
                    return n
        return None

    def prefixes(node):
        out = []

        def walk(n):
            if isinstance(n, ast.If):
                t = n.test
                if (isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute)
                        and t.func.attr == "startswith" and t.args
                        and isinstance(t.args[0], ast.Constant)
                        and isinstance(t.args[0].value, str)
                        and t.args[0].value.startswith("/api/")
                        and t.args[0].value != "/api/"):
                    out.append(t.args[0].value)
                for x in n.body:
                    walk(x)
                for x in n.orelse:
                    walk(x)
        walk(node)
        return out

    gate = api_if(fn[0])
    return (prefixes(gate) if gate else None), (None if gate else "HEAD 版找不到 /api/ 总门")


def table(tree, name):
    n = [x for x in tree.body if isinstance(x, ast.Assign)
         and any(getattr(t, "id", "") == name for t in x.targets)]
    return [ast.literal_eval(e.elts[0]) for e in n[0].value.elts] if n else None


cur = io.open(ROOT / "bot.py", encoding="utf-8").read()
tree = ast.parse(cur)
gets = table(tree, "_API_GET_ROUTES") or []
posts = table(tree, "_API_POST_ROUTES") or []
raws = [x for x in tree.body if isinstance(x, ast.Assign)
        and any(getattr(t, "id", "") == "_API_GET_RAW" for t in x.targets)]
raw_set = set(ast.literal_eval(raws[0].value.args[0])) if raws else set()

print("=== U12e 路由表 vs 重构前 if/elif 链 ===")
for method, name, now in (("do_GET", "_API_GET_ROUTES", gets),
                          ("do_POST", "_API_POST_ROUTES", posts)):
    old, err = old_routes(method)
    chk("%s：旧链前缀与顺序逐条不变" % method,
        old is not None and old == now,
        err or ("旧 %d / 新 %d；差异 %s" % (len(old or []), len(now),
                [(a, b) for a, b in zip(old or [], now) if a != b][:3])))
    chk("%s：条目数固定" % method, len(now) == (31 if method == "do_GET" else 24), len(now))
    chk("%s：无重复前缀" % method, len(now) == len(set(now)))
    chk("%s：每条都是 /api/ 前缀" % method, all(x.startswith("/api/") for x in now))

print("\n=== 顺序陷阱（窄前缀必须先于宽前缀） ===")
for narrow, wide in (("/api/mood_read", "/api/mood"),
                     ("/api/meme_catalog", "/api/meme"),
                     ("/api/meme_preview", "/api/meme"),
                     ("/api/meme_templates", "/api/meme")):
    if narrow in gets and wide in gets:
        chk("%s 在 %s 之前" % (narrow, wide), gets.index(narrow) < gets.index(wide))
    elif narrow in posts and wide in posts:
        chk("%s 在 %s 之前" % (narrow, wide), posts.index(narrow) < posts.index(wide))

print("\n=== dispatcher 与源码级安全守卫 ===")
fget = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "do_GET"][0]
fpost = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "do_POST"][0]
sget = ast.get_source_segment(cur, fget)
spost = ast.get_source_segment(cur, fpost)
chk("do_GET 走 _API_GET_ROUTES 且统一 200 包装",
    "_API_GET_ROUTES" in sget and "self._json(200, _res)" in sget)
chk("do_POST 走 _API_POST_ROUTES 且统一 200 包装",
    "_API_POST_ROUTES" in spost and "self._json(200, _fn(self, data))" in spost)
chk("GET 仍用 parsed path；POST 仍用原始 self.path",
    "urlparse(self.path).path" in sget and "self.path.startswith(_prefix)" in spost)
chk("POST 仍先读 body 再路由（400 优先于 401/404）",
    spost.index("_read_body()") < spost.index("_API_POST_ROUTES"))
for literal, label in (("_looks_like_napcat(", "NapCat 身份闸"),
                       ("drill", "演练参数"),
                       ("_bridge_ok()", "drill 的 bridge token 闸"),
                       ("来路不明", "拒收记账")):
    chk("do_POST 内保留 %s" % label, literal in spost)
chk("/notify 仍在 do_POST 内且带桥 token 门", "/notify" in spost)
chk("GET raw handler 集合与原链 4 个二进制/文件出口一致",
    raw_set == {"/api/meme_preview", "/api/petpet_demo",
                "/api/petpet_frame", "/api/thumb"}, raw_set)
chk("raw 出口不会被 dispatcher 二次包 200",
    "_prefix not in _API_GET_RAW" in sget)

print("\n=== 46 个 action_* 业务签名未被 U12e 改动 ===")
old_src = subprocess.check_output(["git", "show", BASELINE + ":bot.py"], cwd=str(ROOT)).decode("utf-8")
old_tree = ast.parse(old_src)


def action_sigs(t):
    return {n.name: ast.dump(ast.Module(body=[n.args], type_ignores=[]))
            for n in t.body if isinstance(n, ast.FunctionDef)
            and n.name.startswith("action_")}


a, b = action_sigs(old_tree), action_sigs(tree)
chk("action_* 数量不变", len(a) == 46 and len(b) == 46, (len(a), len(b)))
chk("action_* 参数签名逐条不变", a == b, sorted(set(a) ^ set(b))[:5])

print("\nRESULT:", "PASS" if not FAIL else "FAIL (%d 项) %s" % (len(FAIL), FAIL))
sys.exit(1 if FAIL else 0)
