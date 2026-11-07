# -*- coding: utf-8 -*-
"""M0 Golden Set SAFE 守卫。

只读 tests/golden/ 下的冻结文件；不联网、不启动 bot、不发送消息、不调用真实工具。
"""
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8")

FAIL = []


def chk(label, ok, detail=""):
    print(("  [OK] " if ok else "  [失败] ") + label + ("" if ok else " | " + str(detail)))
    if not ok:
        FAIL.append(label)


def main():
    data_path = os.path.join(HERE, "golden_set.json")
    base_path = os.path.join(HERE, "baseline.json")
    cov_path = os.path.join(HERE, "coverage.json")
    eval_path = os.path.join(HERE, "run_eval.py")

    for p in (data_path, base_path, cov_path, eval_path):
        chk("存在 %s" % os.path.basename(p), os.path.exists(p), p)
    if FAIL:
        return 1

    data = json.load(open(data_path, encoding="utf-8"))
    base = json.load(open(base_path, encoding="utf-8"))
    cov = json.load(open(cov_path, encoding="utf-8"))
    n = len(data.get("samples", []))

    chk("样本数 ≥ 200", n >= 200, n)
    chk("样本数与 baseline 一致", base.get("samples") == n, (base.get("samples"), n))
    chk("样本数与 coverage 一致", cov.get("total") == n, (cov.get("total"), n))
    actual = hashlib.sha256(open(data_path, "rb").read()).hexdigest()
    chk("数据集 sha256 与 baseline 一致", actual == base.get("dataset_sha256"),
        (actual[:12], str(base.get("dataset_sha256"))[:12]))
    ids = [s.get("id") for s in data.get("samples", [])]
    chk("样本 ID 唯一", len(ids) == len(set(ids)), len(ids) - len(set(ids)))
    chk("每条都有 expected/forbidden",
        all(s.get("expected", {}).get("actions") and s.get("forbidden", {}).get("actions")
            for s in data.get("samples", [])), "")

    p = subprocess.run([sys.executable, eval_path, "--validate", "--self-test"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    print("  --- run_eval.py ---")
    for line in out.strip().splitlines():
        print("  | " + line)
    chk("run_eval 校验+自测通过", p.returncode == 0, "rc=%d" % p.returncode)

    if FAIL:
        print("\nRESULT: FAIL (%d 项) %s" % (len(FAIL), FAIL))
        return 1
    print("\nRESULT: PASS（%d 条样本，sha256 %s…）" % (n, actual[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
