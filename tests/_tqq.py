"""一键启动（start_qq.bat / _oneclick.py）回归。

跑法：PYTHONIOENCODING=utf-8 python _tqq.py
通过标准：末尾 [OK] 全过、退出码 0。

安全设计：`--check` / `--print-url` / `--no-browser` 全程**不启动 bot、不开浏览器、不杀真 bot**。
唯一例外是第 ⑤ 节：为了验证「定位进程 + kill」这条机制，会起一个**替身监听进程**（127.0.0.1:18099）
并把它结束掉 —— 绝不触碰正在跑的真 bot（测试里对真 pid 只做"定位/识别"，不做 kill）。
用完会删掉 `_tqq_run.txt`。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
BAT = os.path.join(HERE, "start_qq.bat")
ONE = os.path.join(HERE, "_oneclick.py")
RUNLOG = os.path.join(HERE, "_tqq_run.txt")

fails = []


def check(name, cond, extra=""):
    print("  [%s] %s%s" % ("OK" if cond else "失败", name,
                           ("  -- " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def port_open(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def run_one(args, timeout=90):
    r = subprocess.run([PY, "-u", ONE] + args, cwd=HERE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    tok = cfg.get("bridge_token") or ""
    napcat_up = port_open(3000)
    bot_up = port_open(8088)
    print("现场：NapCat 3000=%s  bot 8088=%s  bridge_token 长度=%d\n"
          % ("通" if napcat_up else "不通", "通" if bot_up else "不通", len(tok)))

    print("① start_qq.bat 本体（换行符/编码是踩过坑的红线）")
    raw = open(BAT, "rb").read()
    check("存在且非空", len(raw) > 100, "%d 字节" % len(raw))
    check("CRLF > 0", raw.count(b"\r\n") > 0, raw.count(b"\r\n"))
    check("无裸 LF（cmd 对 LF 的 bat 会错行解析）",
          raw.count(b"\n") - raw.count(b"\r\n") == 0,
          raw.count(b"\n") - raw.count(b"\r\n"))
    check("纯 ASCII（中文全在 py 里）", all(b < 128 for b in raw))
    check("调用 _oneclick.py", b"_oneclick.py" in raw)
    check("没有把逻辑塞进 bat（薄壳）", b"autostart" not in raw and len(raw) < 1200)

    print("\n② --print-url：链接拼装正确，且不回明文 token")
    rc, out = run_one(["--print-url"])
    url = out.strip().splitlines()[-1] if out.strip() else ""
    check("退出码 0", rc == 0, rc)
    check("前缀正确", url.startswith("http://127.0.0.1:8088/admin?token="), url[:56])
    check("回显打码（含 *）", "*" in url)
    check("明文 token（%d 位）没出现在输出里" % len(tok), bool(tok) and tok not in out)

    print("\n③ --check：前置检查（且只读，不启动任何东西）")
    rc, out = run_one(["--check"])
    check("报了 NapCat 状态", "NapCat" in out)
    check("报了 bridge_token 状态但无明文",
          "bridge_token：已配置" in out and tok not in out)
    check("报了 bot 端口", "bot 端口 8088" in out)
    if napcat_up:
        check("NapCat 在线时跳过启动", "跳过启动" in out)
        check("退出码 0", rc == 0, rc)
    else:
        check("NapCat 不在时 check 也不启动（退出码 1）",
              rc == 1 and "只报不做" in out, rc)
    check("check 模式确认未启动进程、未开浏览器", "未启动任何进程" in out)

    print("\n④ 端到端：等 bot 就绪 → 控制台就绪 → 移交保活（不真开浏览器、不杀进程）")
    if not bot_up:
        print("  [跳过] bot 当前没在监听，跳过（避免测试里真的拉起 bot）")
    else:
        with open(RUNLOG, "w", encoding="utf-8") as f:
            p = subprocess.Popen([PY, "-u", ONE, "--no-browser"], cwd=HERE,
                                 stdin=subprocess.DEVNULL,
                                 stdout=f, stderr=subprocess.STDOUT)
        text, hit = "", False
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                text = open(RUNLOG, encoding="utf-8", errors="replace").read()
            except Exception:
                text = ""
            if "控制台就绪" in text:
                hit = True
                break
            time.sleep(1)
        # 它可能已经进 keeper 循环（阻塞），杀掉测试子进程即可
        p.kill()
        p.wait(timeout=10)
        check("'等 bot 就绪 → 控制台就绪' 这条路径通", hit,
              (text or "")[-160:].replace("\n", " | "))
        check("尊重 --no-browser（跳过开浏览器，仍会报就绪）",
              "--no-browser：跳过开浏览器" in text)
        check("NapCat 在线时不重复启动", "跳过启动" in text)
        check("**没有**顺手把正在跑的 bot 杀掉（无 -y 时的默认行为）",
              "照旧用现在这个 bot" in text and "taskkill" not in text)
        check("最后移交 keeper 保活", "移交 keeper 保活" in text)
        try:
            os.remove(RUNLOG)
        except Exception:
            pass

    print("\n⑤ 重启机制：定位进程 + 只杀 python.exe（用替身验证，绝不碰真 bot）")
    import _oneclick as oc
    live = oc.listener_pid(8088)
    check("能从 netstat 定位到在跑的 bot pid（bot.lock 被独占锁定，读不了）", live > 0, live)
    check("确认它是 python.exe（安全阀前提）", oc.is_python(live) is True)
    check("安全阀：非 python.exe 的 pid 一律拒绝，不杀", oc.kill_bot(4) is False)
    check("安全阀：pid=0（没找到）直接返回 False", oc.kill_bot(0) is False)

    decoy_port = 18099
    dec = subprocess.Popen([PY, "-c",
                            "import socket,time;s=socket.socket();"
                            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
                            "s.bind(('127.0.0.1',%d));s.listen(1);time.sleep(120)" % decoy_port])
    # 判定统一走 netstat（kill_bot 依赖的就是它）；连接探测在刚 bind 那一瞬有竞态，别用
    t0 = time.time()
    while time.time() - t0 < 25 and not oc.listener_pid(decoy_port):
        time.sleep(0.5)
    dpid = oc.listener_pid(decoy_port)
    check("替身监听进程已起来（18099，netstat 定位到）", dpid > 0, dpid)
    check("替身端口可连（顺带验连通性）", port_open(decoy_port))
    killed = oc.kill_bot(dpid, decoy_port)
    check("kill_bot 对 python.exe 替身执行成功", killed)
    time.sleep(1.5)
    check("替身确实被结束了", dec.poll() is not None, "poll=%s" % dec.poll())
    check("替身端口已释放", not port_open(decoy_port))
    try:
        dec.kill()
    except Exception:
        pass

    print("\n⑥ 铁律与卫生")
    src = open(ONE, encoding="utf-8").read()
    check("不引入 autostart 开关（铁律 4）", "autostart" not in src)
    check("不引入内容审核（铁律 3）",
          not any(k in src for k in ("moderate", "nsfw", "审核", "敏感词")))
    check("保活没有另写一套（复用 _keeper.main）", "_keeper.main()" in src)
    r = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=HERE,
                       capture_output=True, text=True)
    dirty = [x for x in r.stdout.split("\n") if x.strip()]
    check("_keeper.py / start_all.bat 未被本次改动碰过",
          not any(x in ("_keeper.py", "start_all.bat") for x in dirty),
          "改动中的已跟踪文件: %s" % (dirty or "无"))

    print()
    if fails:
        print("失败 %d 项：%s" % (len(fails), "、".join(fails)))
        print("[失败] 一键启动回归未全过")
        return 1
    print("[OK] 一键启动回归全过（%s）" % time.strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)
