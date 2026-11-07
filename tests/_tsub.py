# -*- coding: utf-8 -*-
"""裸测「先查设定再画」：走真接口，看探测准不准、查到的设定卡长什么样、缓存生不生效。
用法：python _tsub.py  [要查的画面描述 ...]
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import logging
import sys
import time

import bot

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

CASES = [
    "一只赛博朋克猫，霓虹雨夜",
    "花海咲季在演唱会舞台上",
    "秦始皇",
    "明日方舟的黍",
    "美团老鼠",
    "雪山上的经幡",
]


def main():
    cases = sys.argv[1:] or CASES
    probe = bot._draw_llm(None)
    web = bot._draw_webllm(None)
    book = bot._subject_book()
    print("probe=%s web=%s book=%s 缓存已有 %d 条"
          % (bool(probe), bool(web), book, book.size() if book else -1))
    for t in cases:
        t0 = time.time()
        brief, names = book.lookup(t, probe=probe, web=web)
        dt = time.time() - t0
        print("\n>>> %s   （%.1fs，查到 %s）" % (t, dt, "、".join(names) or "没查"))
        if not brief:
            print("    —— 无资料（纯通用描述或没查到）")
            continue
        for line in brief.splitlines():
            print("    " + line)
    # 第二次同样描述：应该只花探测那一下的时间
    if cases:
        t0 = time.time()
        book.lookup(cases[1] if len(cases) > 1 else cases[0], probe=probe, web=web)
        print("\n>>> 再来一次同一条：%.1fs（缓存命中应该很快）" % (time.time() - t0))
    print("缓存现在 %d 条" % book.size())


if __name__ == "__main__":
    main()
