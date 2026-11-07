# -*- coding: utf-8 -*-
"""U12b：现画域（引擎路由 / Studio 排版 / 提交队列 / 耗时统计）。

这一版是**纯移动**：整块源码从 bot.py:8671-10891 原样搬来，不重写业务。
它和别的 qbot_* 模块有一个刻意不同的兼容约定：

* 本模块不 import bot；`install(bot.__dict__)` 把 bot 的模块字典设为宿主全局。
* 调用时未被本模块源码定义的全局名（send_group / CFG / comfy_gen / ...）
  在宿主字典里实时解析 —— 生产代码改了 bot.X 或测试桩改了 bot.X，本模块
  下一行就看到新值；反过来 bot.X = <本模块函数> 后，旧入口继续可用。
* 这是为了让 67 个函数 + 45 个模块级状态在「搬家这一步」可逐字节差分；
  等差分与真链路验收稳定后，再按 U12b 第二批把宿主回调收敛成显式 bind()。

实现说明：把源码放在同目录 `qbot_draw_body.py` 里 exec，是为了让
`inspect.getsource(bot.draw_submit)` 仍能取到与原 bot.py 逐字节相同的源码，
同时把这些 def 的 `__globals__` 换成桥接字典。桥接字典优先返回宿主字典里
后来被覆盖的名字（即测试桩），否则返回随迁实现，故不是两套长期并行实现：
bot.X 和 qbot_draw.X 指向同一个函数对象。
"""
import sys
import types
from pathlib import Path

# 宿主注入（install）；模块被单独 import 时留空字典，未绑即走降级，
# 不会反手 import bot 造成循环依赖。
_HOST = {}
# 源码里显式 `global X` 且第一个写入发生在本模块的名字，要写回宿主字典，
# 让 bot 侧 KNOBS / 旧引用看到的仍是同一个真相。
_HOST_WRITES = {"_draw_switch", "_subject_book_inst"}
# dunder 必须由 exec 自己落到桥接字典，绝不从宿主取（否则 __builtins__ 串线）。
_SPECIAL = {"__name__", "__file__", "__builtins__", "__doc__", "__package__",
            "__loader__", "__spec__"}
# install() 完成后为 True：只有运行期的 `global X` 写入才同步宿主，
# exec 模块级初始化时的同名赋值不该反向污染宿主。
_INSTALLED = False
# 现画域专用 state 目录：默认跟 bot.STATE，测试态由 bot 末尾重绑到临时目录。
# 这里**不**改 bot.STATE（那是全仓共用的路径），只给本模块的名字解析加覆盖，
# 顺手把历史上 `STATE / "subjects.json"` 的测试态落盘漏洞一起堵上。
_STATE_OVERRIDE = None


class _BridgeGlobals(dict):
    """给 LOAD_GLOBAL / STORE_GLOBAL 用的桥接字典。

    Python 的 `global` 读写的是函数 __globals__，不是模块的 __getattr__。
    CPython 对 dict 子类的 LOAD_GLOBAL 会调用 __getitem__，所以这里把
    「测试/宿主后来覆盖的名字」优先返回宿主值；未覆盖的返回随迁实现。
    """

    def __getitem__(self, name):
        if name in _SPECIAL:
            return dict.__getitem__(self, name)
        if name == "STATE" and _STATE_OVERRIDE is not None:
            return _STATE_OVERRIDE
        if name in _HOST:
            return _HOST[name]
        return dict.__getitem__(self, name)

    def __setitem__(self, name, value):
        if name in _SPECIAL:
            return dict.__setitem__(self, name, value)
        dict.__setitem__(self, name, value)
        if _INSTALLED and name in _HOST_WRITES:
            _HOST[name] = value

    def __missing__(self, name):
        if name in _HOST:
            return _HOST[name]
        raise KeyError(name)


def _adopt_functions(namespace):
    """把 exec 生成的顶层函数换 __globals__ 为桥接字典。"""
    for name, obj in tuple(namespace.items()):
        if isinstance(obj, types.FunctionType) and obj.__globals__ is namespace:
            fn = types.FunctionType(obj.__code__, namespace, obj.__name__,
                                    obj.__defaults__, obj.__closure__)
            fn.__kwdefaults__ = obj.__kwdefaults__
            fn.__annotations__ = obj.__annotations__
            fn.__qualname__ = obj.__qualname__
            fn.__doc__ = obj.__doc__
            fn.__module__ = __name__
            dict.__setitem__(namespace, name, fn)


def install(host_globals, state_dir=None):
    """由 bot.py 在 import 后调用：把 bot 的全局字典接成宿主。

    host_globals 必须是**活字典**（bot.__dict__），不能是 vars(bot) 的副本；
    CFG/STATE/comfy_gen 等热更与测试桩都靠这个引用实时可见。
    """
    global _HOST, _INSTALLED, _STATE_OVERRIDE, _G
    _HOST = host_globals
    _STATE_OVERRIDE = None
    _G = _BridgeGlobals()
    _G["__name__"] = __name__
    _G["__file__"] = str(Path(__file__).with_name("qbot_draw_body.py"))
    _G["__builtins__"] = __builtins__
    # 用桥接字典重跑整块源码：源码里的 global 读写、模块级常量、内部互调
    # 全部保持原来的 Python 语义，只是名字解析转去宿主。
    body_file = Path(__file__).with_name("qbot_draw_body.py")
    body = body_file.read_text(encoding="utf-8")
    exec(compile(body, str(body_file), "exec"), _G)
    _adopt_functions(_G)
    # 把 67 个函数 + 45 个全局注册回 bot，旧 bot.X 入口不变；测试随后
    # 即使替换 bot.draw_submit / bot._draw_q，桥接字典也会实时看见。
    for name, value in tuple(_G.items()):
        if name in _SPECIAL:
            continue
        _HOST[name] = value
    _INSTALLED = True
    # 暴露同一批函数/常量给 `import qbot_draw` 的调用方；宿主字典仍是运行时真相。
    setattr(sys.modules[__name__], "_install_exports", True)
    for name, value in tuple(_HOST.items()):
        if name in _G and name not in _SPECIAL:
            setattr(sys.modules[__name__], name, value)
    # 落盘路径跟 state 走（测试态由 bot 末尾统一重绑到临时目录）。
    # 不改宿主全局 STATE：bot.py / 其它模块仍按自己原来的 STATE 解析。
    if state_dir is not None:
        _STATE_OVERRIDE = Path(state_dir)
        _HOST["DRAW_TIME_FILE"] = _STATE_OVERRIDE / "draw_time.json"
        dict.__setitem__(_G, "DRAW_TIME_FILE", _HOST["DRAW_TIME_FILE"])
        setattr(sys.modules[__name__], "DRAW_TIME_FILE", _HOST["DRAW_TIME_FILE"])
    return sys.modules[__name__]


def bind(state_dir=None):
    """U4 隔离契约：`state_dir` 重绑现画域的落盘路径，不重建函数对象。"""
    global _STATE_OVERRIDE
    if state_dir is None:
        return
    _STATE_OVERRIDE = Path(state_dir)
    if _HOST:
        _HOST["DRAW_TIME_FILE"] = _STATE_OVERRIDE / "draw_time.json"
        dict.__setitem__(_G, "DRAW_TIME_FILE", _HOST["DRAW_TIME_FILE"])
        setattr(sys.modules[__name__], "DRAW_TIME_FILE", _HOST["DRAW_TIME_FILE"])


# install() 之前也能 import，但函数尚未注入；这是有意的：bot.py 的
# `_bind_modules()` 和 import bot 的测试都会显式调用 install(bot.__dict__)。
_G = _BridgeGlobals()
