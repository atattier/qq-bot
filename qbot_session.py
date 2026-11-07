# -*- coding: utf-8 -*-
"""qbot_session —— 会话总控（2026-10-26 主人要求）。

原来「生图链路」卡只管 comfyui / moss 两条链路按群开关。主人要的是**一张表管全部**：
谁响应、能不能画（两条链路）、能不能调本地工具、用哪个人格 —— 群聊和私聊都在同一张表里。

所以这里只做「一个会话（群号 / 私聊）能用什么」的**单一事实来源**：

    config.json
      "session": {
        "groups":  {"123450001": {"ai": true, "sim": true, "tools": true, "persona": ""}},
        "private": {"123450003": {"ai": true, "tools": false, "persona": "小鲸",
                                   "comfyui": false, "moss": true}},
        "_说明": "..."
      }

    ⚠️ 私聊是**按人**存的（2026-10-26 主人：「私聊要分开，每个成员都不同」）：
       键是对方 QQ 号，不是一坨「所有私聊」。谁关谁开互不影响。

字段含义（缺省 = 全开，跟以前的行为一致，不改配置就等于没这层）：
    ai       这个群要不要理（⚠️ 群这一列真正存的是 `config.groups` 白名单，不在这里）
    sim      仿真群友能不能主动插话 / 冷场开场
    tools    能不能调本地工具（agent 大脑 + skills 提示词）
    persona  人格文件名（roles/<name>.md），空 = 跟随全局人格

🔴 为什么 comfyui / moss 不搬进来：那两条链路的开关**已经**是
   `draw.comfyui.groups` / `draw.moss.groups`（2026-10-24 定的），群里还有
   「comfyui 开/关」「mossapi 开/关」口令直接写它。搬一份进来就有两个真相，
   下次谁改了哪边都说不清。网页上的总控表把两边**读在一起给人看**，
   写还是各回各家 —— 这里只负责 ai / sim / tools / persona 这四列。

⚠️ 本模块不 import bot，也不 import 别的业务模块（qbot_persona 会反过来用它），
   纯函数 + 一份绑定的 CFG 引用，宿主能力（save_config）靠 bind() 注入。
"""
import logging

log = logging.getLogger("qqbot.session")

FIELDS = ("ai", "sim", "tools", "comfyui", "moss")

# 缺省值：全开 —— 没配过 config 的老机器行为不变
# ⚠️ comfyui / moss **不在缺省表里**：私聊这两条链路没单独配时要往下落到
#    `draw.<engine>.private` / `draw.private`，所以「没配」和「配了 true」必须能区分，
#    用 raw()/has() 读，别用 get()。
_DEFAULTS = {"ai": True, "sim": True, "tools": True, "persona": ""}

CFG = {}
_save = None


def bind(cfg, save=None):
    """bot.py 启动时调用。cfg 是全局配置字典引用（全程不重新绑定，安全）。"""
    global CFG, _save
    if cfg is not None:
        CFG = cfg
    if save:
        _save = save


def cfg():
    """`session` 段本体。**只补说明，不预建 groups / private 空字典** ——
    预建会在每次 save_config 时把 `"groups": {}`、`"private": {}` 写回 config，
    主人打开配置文件看到的是一堆空壳（2026-10-26 实测踩到）。空壳由 `_prune` 收掉。"""
    d = CFG.setdefault("session", {})
    doc = ("会话总控（2026-10-26）：每个群、每个私聊成员能不能用插话(sim)、本地工具(tools)、"
           "用哪个人格(persona，空=跟随全局)。私聊**按人存**（private 的键=对方QQ），"
           "群是否响应仍看 groups 白名单；群里两条链路存 draw.<engine>.groups，"
           "私聊存本段 comfyui/moss（没配就落 draw.<engine>.private）。网页「会话总控」卡可改。")
    # 文案升级过（私聊从「所有人一行」改成「按人一行」）：老 config 里的旧说明要跟着换，
    # 否则主人翻配置文件看到的还是「每个群/私聊」这种过时描述。
    if not d.get("_说明") or "按人存" not in str(d.get("_说明")):
        d["_说明"] = doc
    return d


def key_of(gid):
    """会话 key 归一：私聊 = `priv:<对方QQ>`，群 = 群号字符串。

    私聊的会话 key 是负数（`priv_key(uid)`），所以 `-gid` 就是对方 QQ 号。
    """
    try:
        g = int(gid)
    except (TypeError, ValueError):
        return ""
    return ("priv:%d" % -g) if g < 0 else str(g)


def _node(gid, create=False):
    """取某个会话的配置节点。私聊**按人**一个节点（键=对方 QQ）。"""
    d = cfg()
    key = key_of(gid)
    if not key:
        return {}
    if key.startswith("priv:"):
        uid = key[5:]
        priv = d.setdefault("private", {}) if create else (d.get("private") or {})
        if create:
            return priv.setdefault(uid, {})
        return priv.get(uid) or {}
    grp = d.setdefault("groups", {}) if create else (d.get("groups") or {})
    if create:
        return grp.setdefault(key, {})
    return grp.get(key) or {}


def get(gid, field, default=None):
    """读某个会话的开关。field 只认 FIELDS；没配过就回默认（全开）。"""
    if field not in FIELDS:
        return default
    v = _node(gid).get(field)
    if v is None:
        return _DEFAULTS[field] if default is None else default
    return bool(v)


def flag(gid, field, default=True):
    """`get` 的显式默认版（调用点写清楚自己想要什么默认）。"""
    return get(gid, field, default)


def raw(gid, field):
    """**没配过返回 None**（而不是默认值）—— 私聊两条链路要靠这个区分
    「这个人单独关了」和「没单独配，跟着全局走」。"""
    return _node(gid).get(field)


def has(gid, field):
    return raw(gid, field) is not None


def set_flag(gid, field, value):
    """写一个开关。**写回默认值（True）= 删掉这条**，别在 config 里堆一堆 `true`：
    缺省就是全开，留个 `"sim": true` 只会让人以为这里有什么特别设置。"""
    if field not in FIELDS:
        raise ValueError("field 只能是 %s" % " / ".join(FIELDS))
    node = _node(gid, create=True)
    if field in _DEFAULTS and bool(value) == _DEFAULTS[field]:
        node.pop(field, None)      # 写回默认 = 删键，config 里不留 `"tools": true`
    else:
        node[field] = bool(value)
    _prune(gid, node)
    if _save:
        _save()
    log.info("会话总控: %s.%s = %s", key_of(gid) or gid, field, bool(value))
    return {field: bool(value)}


def _prune(gid, node):
    """节点空了就摘掉，别留 `"123450001": {}` / `"123450003": {}` 这种空壳。"""
    if node:
        return
    d = cfg()
    key = key_of(gid)
    if key.startswith("priv:"):
        priv = d.get("private") or {}
        priv.pop(key[5:], None)
        if not priv:
            d.pop("private", None)
        return
    if key:
        (d.get("groups") or {}).pop(key, None)
    if not (d.get("groups") or {}):
        d.pop("groups", None)


def persona_of(gid):
    """这个会话指定的人格名（roles/<name>.md），空串 = 跟随全局。"""
    return str(_node(gid).get("persona") or "").strip()


def unset(gid, field):
    """删掉这个会话的某项配置（回到「没单独配」）。私聊链路要跟全局默认对齐时用。"""
    node = _node(gid, create=True)
    node.pop(field, None)
    _prune(gid, node)
    if _save:
        _save()
    log.info("会话总控: %s.%s 恢复默认", key_of(gid) or gid, field)


def set_persona(gid, name):
    node = _node(gid, create=True)
    name = str(name or "").strip()
    if name:
        node["persona"] = name
    else:
        node.pop("persona", None)
    _prune(gid, node)
    if _save:
        _save()
    log.info("会话总控: %s 人格 -> %s", key_of(gid) or gid, name or "跟随全局")
    return {"persona": name}


def snapshot(gid):
    """给网页用的一行状态。"""
    return {"ai": get(gid, "ai", True), "sim": get(gid, "sim", True),
            "tools": get(gid, "tools", True), "persona": persona_of(gid)}
