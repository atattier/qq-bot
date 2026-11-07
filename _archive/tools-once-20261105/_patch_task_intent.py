# -*- coding: utf-8 -*-
"""任务意图路由（2026-10-29 晚）：主人派活 ⇒ agent 上岗，聊天大脑只管陪聊。

病（18:38 私聊实测）：
  主人私聊「你去调戏一下那个狐狐，探索一下她这个bot的使用方法，探索完毕找我报道」
  ⇒ 「私聊大脑生效：这条走 custom（跳过 agent/dsh）」⇒ 聊天大脑没有工具，
  只能在私聊里「收到♡这就去戳狐狐两下」演戏，全程 @不到群里的狐狐。
  主人连纠三次（去群里探索啊 / 我们现在在私聊里啊 / 所以让你去群聊里找她聊天）。

根因两层：
  ① `private_provider=custom` 在 ai_reply() 开头一票否决 agent/dsh（10-29 早主人要的
     「私聊自定义 API」），派活消息也被它吃掉；
  ② 全局大脑也换成了 custom，agent_selected()=False —— 就算 ① 放行，agent 也轮不到。

修：
  1) 新增 `_task_intent()` 保守词表判定「这句话是派活不是闲聊」；
  2) 仅主人生效：命中 ⇒ 私聊大脑让位、agent 强制上岗（不看 agent_selected）；
  3) 后台开关 `agent.task_intent`（默认开，放「系统」组 —— 大脑组 ≤10 顶格别碰）。
"""
import io
import os

B = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "bot.py")
s = io.open(B, encoding="utf-8").read()


def rep(old, new, tag):
    c = s.count(old)
    assert c == 1, "%s 锚点命中 %d" % (tag, c)
    return s.replace(old, new)


# ---------- 1) 词表 + 判定函数（插在 private_brain 之后、chat 之前） ----------
old1 = '''def chat(messages, question="", force_search=False, provider=None):'''
new1 = '''# —— 任务意图（2026-10-29 晚）：主人派活 ⇒ agent 上岗 ——
# 词表从主人真实指令提炼（「去调戏一下那个狐狐，探索一下她这个bot的使用方法，
# 探索完毕找我报道」），刻意保守：宁可少判（漏判=bot 演戏不干活，主人再骂一轮），
# 别乱判（误判成本只是 agent 慢几秒，而且 agent 本来就能聊天）。
_TASK_RE = re.compile(
    r"去(群里|群聊)|群里(找|问|戳|撩|探索|调戏|聊)"
    r"|探索|试探|调戏一?下|戳一戳|撩一撩"
    r"|报[道到]|汇报"
    r"|帮我(查|搜|找|问|发|算|写|整理|下载|上传|盯着|监控|留意|跑|办|看|试)"
    r"|(查|搜)一(下|搜)|查查|搜搜"
    r"|提醒我|发到群里|帮我传话")


def _task_intent(content):
    """这句话是不是「派活」而不是闲聊。只做文本判定；仅主人生效在路由处限。"""
    t = str(content or "").strip()
    if not t or len(t) < 4:
        return False
    return bool(_TASK_RE.search(t))


def _task_intent_enabled():
    c = agent_cfg()
    # 默认开；显式 false 要能关掉 —— 用 get 默认参数，别用 or（0/False 会被吞）
    return c.get("task_intent", True)


def chat(messages, question="", force_search=False, provider=None):'''
s = rep(old1, new1, "插函数")

# ---------- 2) 路由开头：私聊大脑让位 ----------
old2 = '''    _pb = private_brain(group_id)
    if _pb:
        log.info("私聊大脑生效：这条走 %s（跳过 agent/dsh）", _pb)'''
new2 = '''    _pb = private_brain(group_id)
    # 任务意图（2026-10-29 晚）：主人派活（「去群里找狐狐探索」）需要 agent 的手，
    # 聊天大脑只会「收到♡这就去」地演戏 —— 私聊大脑让位、agent 强制上岗。
    _task = bool(_task_intent(content)) and is_master(uid) and _task_intent_enabled()
    if _pb and _task:
        log.info("私聊大脑让位：这条命中任务意图，转 agent 大脑干活")
        _pb = None
    if _pb:
        log.info("私聊大脑生效：这条走 %s（跳过 agent/dsh）", _pb)'''
s = rep(old2, new2, "路由开头")

# ---------- 3) 两个 skip 日志分支：agent_selected() or _task ----------
old3 = '''    if not _pb and not _tools_ok and agent_selected() and agent_enabled():
        log.info("(会话总控：%s 关了本地工具，这轮跳过 agent 大脑)", group_id)
    elif not _pb and not _owner_ok and agent_selected() and agent_enabled():
        log.info("(仅主人可驱动 agent：%s 不是主人，这轮回落 API 大脑)", uid or "?")'''
new3 = '''    if not _pb and not _tools_ok and (agent_selected() or _task) and agent_enabled():
        log.info("(会话总控：%s 关了本地工具，这轮跳过 agent 大脑)", group_id)
    elif not _pb and not _owner_ok and (agent_selected() or _task) and agent_enabled():
        log.info("(仅主人可驱动 agent：%s 不是主人，这轮回落 API 大脑)", uid or "?")'''
s = rep(old3, new3, "skip分支")

# ---------- 4) agent 主分支：放行 _task ----------
old4 = '''    if (not _pb) and agent_selected() and agent_enabled() and _tools_ok and _owner_ok:'''
new4 = '''    if (not _pb) and (agent_selected() or _task) and agent_enabled() and _tools_ok and _owner_ok:'''
s = rep(old4, new4, "agent主分支")

# ---------- 5) 开关不做旋钮：「系统」组已在 10-26 后重构消失，加新组要同步
# CATS/CAT_GROUPS/CATKEYS/_thttp 断言，动静大且不紧急。config 字段
# agent.task_intent 默认开（_task_intent_enabled 里 get 默认 True），想关写 false 即可。

io.open(B, "w", encoding="utf-8", newline="").write(s)
print("任务意图路由补丁已写入")
