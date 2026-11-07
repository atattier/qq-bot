# -*- coding: utf-8 -*-
"""/admin 页面 JS 语法守卫（2026-10-29）。

事故背景：给「自定义API 模型」加模型选择窗时，内层三元少写了个 :"",
整个 <script> 解析失败 ⇒ 页面只剩「01 概览 / 02 预设档」两个静态区块，
其余 17 个分组全部消失。以后凡是改了 admin_html.py 的脚本段，回归必跑本测试：
把 <script> 抽出来喂给 `node --check`（node 不在就用括号配平兜底，不误报）。
"""
import os, re, shutil, subprocess, sys, tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

bad = 0
html = open(os.path.join(_ROOT, "qbot", "admin_html.py"), encoding="utf-8").read()

# 1) 页面骨架还在
for anchor in ("__TOKEN__", "</style>", "openModelPick", "id=\"app\"" if False else "<script>"):
    if anchor not in html:
        print("FAIL 页面缺关键片段: %r" % anchor); bad += 1
    else:
        print("OK 页面含 %r" % anchor)

m = re.search(r"<script>(.*)</script>", html, re.S)
if not m:
    print("FAIL 抽不出 <script> 段"); sys.exit(1)
js = m.group(1)

# 2) node --check（最权威）；找不到 node 就退回括号/引号配平启发式
node = shutil.which("node")
if not node:
    for p in (os.environ.get("QBOT_NODE") or "",
              r"C:\Program Files\nodejs\node.exe"):
        if os.path.exists(p):
            node = p; break
if node:
    tmp = os.path.join(tempfile.gettempdir(), "qbot_admin_check.js")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
    if r.returncode == 0:
        print("OK node --check 通过（%d 字符脚本）" % len(js))
    else:
        print("FAIL node --check 报错：")
        print((r.stderr or r.stdout or "")[-1500:])
        bad += 1
    try:
        os.remove(tmp)
    except OSError:
        pass
else:
    # 兜底：只查括号配平（字符串里的括号会干扰，先把字符串字面量清掉）
    s = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`', "", js)
    s = re.sub(r"//[^\n]*|/\*.*?\*/", "", s, flags=re.S)
    for o, c in (("{", "}"), ("(", ")"), ("[", "]")):
        if s.count(o) != s.count(c):
            print("FAIL %s%s 不配平: %d vs %d" % (o, c, s.count(o), s.count(c))); bad += 1
        else:
            print("OK %s%s 配平 (%d)" % (o, c, s.count(o)))
    print("WARN node 不在 PATH，只做了配平兜底（建议装 node 后重跑）")

print("=" * 10, "失败 %d 条" % bad)
sys.exit(1 if bad else 0)
