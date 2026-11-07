# -*- coding: utf-8 -*-
"""黑话 / 网络用语库（2026-10-10 从 bot.py 抽出，原 L3602-3797）。

抽出规则：**本模块不 import bot。** 宿主能力全部靠 bind() 注入，
这样它能被单独导入测试，也不会和 bot.py 形成循环依赖。

宿主注入的钩子（bot.py 启动时调用 bind()）：
    send_group(gid, text)     发群消息
    context_block(gid, skip)  取最近聊天上下文
    chat(messages)            调 LLM，返回 (?, answer)
    cfg                       全局配置字典（传引用，别传快照）

⚠️ 三个容易踩回去的坑，改这块前先读：
1. `SLANG` 是宿主持有的可变字典引用，只能用 clear()+update() 原地改。
   一旦写 `global SLANG; SLANG = {...}` 重新绑定，宿主那边 from-import 拿到的是旧对象，
   表现为「黑话入库了但读不到」，而且不报错。
2. `slang_extract` 走独立线程，别在持有锁的时候调 LLM。
3. 注入钩子可能没配（比如测试里只导ajar用算法部分），降级函数是 no-op 不是抛异常。
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / "state"

log = logging.getLogger("qqbot")

SLANG_FILE = STATE / "slang.json"
SLANG = {"terms": {}}
_slang_lock = threading.Lock()
_slang_state = {"since": 0, "last": 0.0, "running": False}

# ---- 宿主钩子（未 bind 时降级为空操作，方便单测）----
_send = None
_context = None
_chat = None
CFG = {}


def _noop_send(*args, **kwargs):
    """没 bind 时的降级：只记日志，别把 bot 拖崩。"""
    log.warning("黑话模块想发消息但没注入 send 钩子，丢弃：%s", (args[1] if len(args) > 1 else "")[:60])


def _emit(gid, text):
    """统一出口：有钩子真发，没钩子降级记日志。"""
    (_send or _noop_send)(gid, text)


def bind(send=None, context=None, chat=None, cfg=None, state_dir=None):
    """由 bot.py 在启动时调用，注入宿主能力。重复调用以最后一次为准。

    state_dir（2026-11-05 U4）：重定向 slang.json 落点——测试态隔离用，与
    mood/expression/timing 等模块的 bind(state_dir=...) 同一约定。不传 = 现行为。
    """
    global _send, _context, _chat, CFG, SLANG_FILE
    if send is not None:
        _send = send
    if context is not None:
        _context = context
    if chat is not None:
        _chat = chat
    if cfg is not None:
        CFG = cfg
    if state_dir is not None:
        SLANG_FILE = Path(str(state_dir)) / "slang.json"
    return CFG


def _cfg():
    """配置可能已被热替换，每次取一次最新的引用。"""
    return CFG


def terms_snapshot(deep=False):
    """SLANG['terms'] 的加锁快照，给宿主只读展示用（2026-11-05 U11：原来 bot 直接
    from-import 私有 `_slang_lock` 自己锁——锁的语义散在两个模块里，谁改谁知道）。
    deep=True 时每个 term 也拷一层（要改/要序列化整表时用）。"""
    with _slang_lock:
        if deep:
            return {k: dict(v) for k, v in SLANG["terms"].items()}
        return dict(SLANG["terms"])


# ---------------- 读写 ----------------
def load_slang():
    """读盘。⚠️ 必须原地更新 SLANG（见模块 docstring 第 1 条）。"""
    try:
        if SLANG_FILE.exists():
            data = json.loads(SLANG_FILE.read_text(encoding="utf-8"))
        else:
            data = None
    except Exception as e:
        log.warning("读 slang.json 失败: %s", e)
        data = None
    if not isinstance(data, dict):
        data = {"terms": {}}
    data.setdefault("terms", {})
    with _slang_lock:
        SLANG.clear()
        SLANG.update(data)


def save_slang():
    # 原子写 + 上一版留 .prev（2026-11-03 数据事故教训：测试曾把真词库整文件覆盖，
    # 当时没有任何可捞的备份）。写崩不毁文件，写错还能回退一步。
    try:
        SLANG_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(SLANG, ensure_ascii=False, indent=1)
        if SLANG_FILE.exists():
            try:
                import shutil
                shutil.copyfile(SLANG_FILE, SLANG_FILE.with_name(SLANG_FILE.name + ".prev"))
            except Exception as e:  # noqa: BLE001
                log.debug("slang .prev 留存失败（不影响主流程）: %s", e)
        tmp = SLANG_FILE.with_name(SLANG_FILE.name + ".tmp.%s" % os.getpid())
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, SLANG_FILE)
    except Exception as e:
        log.error("写 slang.json 失败: %s", e)


# ---------------- 增删查 ----------------
def slang_learn(term, meaning, status="confirmed", source=""):
    term = re.sub(r"\s+", "", term or "")[:24]
    if not term or not meaning:
        return
    with _slang_lock:
        t = SLANG["terms"].get(term, {"count": 0})
        t.update({"meaning": meaning.strip()[:300], "status": status,
                  "source": source, "t": time.time()})
        t["count"] = int(t.get("count", 0)) + 1
        SLANG["terms"][term] = t
    save_slang()
    log.info("黑话入库[%s] %s = %s", status, term, meaning[:60])


def slang_bump(term):
    with _slang_lock:
        t = SLANG["terms"].get(term)
        if t:
            t["count"] = int(t.get("count", 0)) + 1
            save_slang()


def slang_block(limit=12):
    with _slang_lock:
        items = [t for t in SLANG["terms"].items() if t[1].get("status") == "confirmed"]
    items.sort(key=lambda kv: -int(kv[1].get("count", 0)))
    return "\n".join("%s=%s" % (k, (v.get("meaning") or "")[:80]) for k, v in items[:limit])


# ---------------- A2 四件套（2026-11-03 人味阶段）：证据 / 阈值推断 / MANUAL 保护 / 语境释义 ----------------
# 参考 MaiBot 的黑话管线自己实现的四件事：
#   1. slang_observe     —— 群消息里出现已知黑话就计数 + 记真实来源（mid），证据封顶 8 条
#   2. 阈值推断          —— 次数跨过 4/8/25/100 才让模型猜一次，字面义 vs 语境义分开问，
#                          猜的结果永远 pending（阈值触发 ≠ 事实确认）
#   3. MANUAL 保护       —— 管理员/联网确认过的词 AI 永远不覆盖（写回前还会再查一遍）
#   4. slang_guess_block —— 只解释**当前聊天里真实出现的**词，猜的标明是猜的；
#                          开关 slang.context_meaning 关掉 = 完全恢复旧行为
INFER_THRESHOLDS = (4, 8, 25, 100)   # 跨档才触发推断（MaiBot 同款参考阈值）
INFER_WINDOW = 3                     # 推断时最多带最近几条证据
GUESS_MAX = 8                        # 语境释义最多注入几个词
GUESS_CHARS = 600                    # 语境释义总字符预算
_infer_running = 0


def _is_manual(t):
    """管理员/联网确认过的词拥有最高优先级，AI 推断永远不许碰。"""
    return (not t) or t.get("status") == "confirmed" or t.get("source") == "管理员确认"


def _infer_needs(t):
    """跨过一条新阈值、且还不是已确认词条 ⇒ 该推断一次了。"""
    if not t or _is_manual(t):
        return False
    cnt, last = int(t.get("count", 0)), int(t.get("inferred_at_count", 0))
    return any(last < th <= cnt for th in INFER_THRESHOLDS)


def slang_observe(gid, mid, text):
    """群消息扫一眼（bot.py 消息入口调用，私聊不进黑话库、bot 自己的话不走这里）：
    出现已知黑话就 +1 并记真实来源（mid + 原文截段）。只认 ≥2 字的词——
    「你」「哦」这种单字几乎每条消息都命中，纯噪音；rejected 不计。
    总开关关着 = 完全不观察（回到旧行为：只有联网答案和管理员口令能动词库）。
    跨阈值的词顺手触发一次自动推断（独立线程，不占消息主链路）。"""
    text = str(text or "")
    if not text or not SLANG["terms"]:
        return
    if not _cfg().get("slang", {}).get("enabled", True):
        return
    with _slang_lock:
        cands = [k for k, t in SLANG["terms"].items()
                 if len(k) >= 2 and k in text and t.get("status") != "rejected"]
        if not cands:
            return
        infer = []
        for k in cands:
            t = SLANG["terms"][k]
            t["count"] = int(t.get("count", 0)) + 1
            ev = t.setdefault("evidence", [])
            ev.append({"gid": gid, "mid": mid, "text": text[:80], "t": time.time()})
            del ev[:-8]   # 证据只留最近 8 条，全是从真实消息里抄的，不编
            if _infer_needs(t):
                infer.append(k)
        save_slang()
    for k in infer:
        slang_maybe_infer(k)


def _pick_meaning(text):
    """模型回答里抠 meaning 字段；抠不出就把原文当答案（诚实降级）。"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return str((json.loads(m.group(0)) or {}).get("meaning") or "").strip()
        except Exception:
            pass
    return (text or "").strip()


def _norm_meaning(s):
    return re.sub(r"[\s，。,.！!？?、；;：:\"'「」（）()]", "", str(s or ""))


def slang_infer_once(term):
    """A2 阈值推断（同步版，测试可以直接打桩 _chat 跑）：
    字面义和语境义**分开问**——两个答案一致 ⇒ 就是普通词不是黑话（只记「推断过」，不写释义）；
    不一致 ⇒ 把语境义写进词条，但 status 仍是 pending、meaning_confirmed 恒为 False：
    转正只有管理员「黑话 确认 词 意思」一条路。
    写回前再查一遍确认状态——推断跑的这几秒里管理员可能刚好拍完板。"""
    if not _chat:
        return None
    with _slang_lock:
        t = SLANG["terms"].get(term)
        if not t or not _infer_needs(t):
            return None
        ev = [str((e or {}).get("text") or "") for e in (t.get("evidence") or [])[-INFER_WINDOW:]]
    if not ev:
        return None
    try:
        _, face = _chat([
            {"role": "system", "content": "你是词义分析器。只输出 JSON：{\"meaning\":\"…\"}。"},
            {"role": "user", "content":
                "单独看「%s」这个词，不考虑任何聊天语境，它最常见的意思是什么？一句话不超过40字。" % term}])
        _, withc = _chat([
            {"role": "system", "content":
                "你在帮 QQ 群机器人理解群友的话。只输出 JSON：{\"meaning\":\"…\"}，拿不准就输出 {\"meaning\":\"\"}。"},
            {"role": "user", "content":
                "群里「%s」出现过多次，相关聊天：\n%s\n\n结合这些聊天，它在这里最可能是什么意思？"
                "一句话不超过40字。" % (term, "\n".join("- " + e for e in ev))}])
    except Exception as e:
        log.warning("黑话推断失败[%s]: %s", term, e)
        return None
    face_m, ctx_m = _pick_meaning(face), _pick_meaning(withc)
    if not ctx_m:
        return None
    same = _norm_meaning(face_m) == _norm_meaning(ctx_m)
    if not same and face_m:
        # 字符串不一致≠意思不同（措辞差异太常见，实测「显卡」被误分过档）。
        # 补一道语义仲裁：让模型判 A/B 是不是同一个意思；仲裁本身挂了就退回字符串比对。
        try:
            _, judge = _chat([
                {"role": "system", "content": "只输出 JSON：{\"same\":true} 或 {\"same\":false}。"},
                {"role": "user", "content":
                    "下面两个对「%s」的解释是不是同一个意思？\nA: %s\nB: %s" % (term, face_m, ctx_m)}])
            jm = re.search(r"\{.*\}", judge or "", re.S)
            if jm:
                same = bool((json.loads(jm.group(0)) or {}).get("same"))
        except Exception as e:  # noqa: BLE001
            log.debug("黑话推断仲裁失败（退回字符串比对）: %s", e)
    with _slang_lock:
        cur = SLANG["terms"].get(term)
        if not cur or _is_manual(cur):
            return None   # 管理员刚确认过 → AI 住手（MANUAL 保护）
        cur["inferred_at_count"] = int(cur.get("count", 0))
        if same:
            cur["meaning_src"] = "literal"   # 字面义：不是黑话，不写释义、不注入
        else:
            cur["meaning"] = ctx_m.strip()[:300]
            cur["meaning_src"] = "inferred"
            cur["meaning_confirmed"] = False   # 阈值触发 ≠ 事实确认
        save_slang()
    return "literal" if same else "inferred"


def slang_maybe_infer(term):
    """推断的异步壳：开关没开/同时跑太多/不满足阈值就空转。"""
    global _infer_running
    sc = _cfg().get("slang", {})
    if not (sc.get("enabled", True) and sc.get("auto_infer", True)):
        return
    with _slang_lock:
        t = SLANG["terms"].get(term)
        if not t or not _infer_needs(t) or _infer_running >= 2:
            return
        _infer_running += 1

    def run():
        global _infer_running
        try:
            slang_infer_once(term)
        finally:
            with _slang_lock:
                _infer_running -= 1

    threading.Thread(target=run, daemon=True).start()


def slang_context_hits(text, limit=None):
    """当前上下文文本里**真实出现**的待确认词 → [(term, meaning, is_guess)]。
    只认子串命中（不相关黑话绝不进 Prompt）；confirmed/rejected 都不算「猜义」——
    前者走正式黑话块（事实），后者是管理员明确不要的；长词优先（更可能是真黑话）。"""
    text = str(text or "")
    if not text:
        return []
    with _slang_lock:
        hits = []
        for k, t in SLANG["terms"].items():
            if len(k) < 2 or k not in text or t.get("status") != "pending":
                continue
            m = str(t.get("meaning") or "").strip()
            if not m:
                continue
            # 释义是管理员/联网确认过的 → 是事实；其余（AI 推断的）→ 标「猜的」
            hits.append((k, m, not _is_manual(t), int(t.get("count", 0))))
    hits.sort(key=lambda x: (-len(x[0]), -x[3]))
    return [(k, m, g) for k, m, g, _ in hits[:int(limit or GUESS_MAX)]]


def slang_guess_block(text, limit=None):
    """A2 语境释义注入：只解释当前聊天里出现过的词，猜的标明是猜的。
    开关 slang.context_meaning 关掉 → 返回空串，恢复旧行为。"""
    if not (_cfg().get("slang", {}).get("context_meaning", True)):
        return ""
    hits = slang_context_hits(text, limit)
    if not hits:
        return ""
    lines, budget = [], GUESS_CHARS
    for k, m, guess in hits:
        line = "%s=%s%s" % (k, str(m)[:80], "（这个意思是猜的，不一定对）" if guess else "")
        if len(line) > budget:
            break
        lines.append(line)
        budget -= len(line)
    if not lines:
        return ""
    log.info("黑话语境释义注入: %s", "、".join(l.split("=")[0] for l in lines))
    return ("【语境里的词（这些词在刚才的聊天里出现过，解释只是帮你听懂；"
            "标了「猜的」不一定对；别硬用别跟风，有出入时以聊天本身为准）】\n" + "\n".join(lines))


TERM_Q_RE = re.compile(r"([0-9A-Za-z\u4e00-\u9fff]{1,12}?)(?:这个|那个)?(?:是|算)?"
                       r"(?:什么梗|啥梗|什么意思|啥意思|什么玩意|什么水平)")


def slang_learn_from_answer(question, answer):
    """联网搜过的「X是什么梗」直接入库，下次不用再看。"""
    m = TERM_Q_RE.search(question or "")
    if not m:
        return
    term = m.group(1).strip(" ，,。?？!！的")
    if not 1 <= len(term) <= 12:
        return
    with _slang_lock:
        known = term in SLANG["terms"]
    if known:
        slang_bump(term)
    else:
        slang_learn(term, answer, "confirmed", "联网")


SLANG_CMD_RE = re.compile(r"^黑话(?:\s*(列表|确认|拒绝|删除|学习))?\s*(.*)$")


# ---------------- 群口令「黑话 ...」 ----------------
def handle_slang(gid, raw, privileged):
    m = SLANG_CMD_RE.match(raw)
    act = m.group(1) or "列表"
    arg = (m.group(2) or "").strip()
    with _slang_lock:
        terms = dict(SLANG["terms"])
    if act == "列表":
        conf = [k for k, v in terms.items() if v.get("status") == "confirmed"]
        pend = [k for k, v in terms.items() if v.get("status") == "pending"]
        _emit(gid, "已确认 %d 个：%s\n待确认 %d 个：%s" % (
            len(conf), "、".join(conf[:15]) or "无", len(pend), "、".join(pend[:10]) or "无"))
        return
    if act == "学习":
        if not privileged:
            _emit(gid, "这个得管理员来。")
            return
        _emit(gid, "我去翻翻最近聊天记录，有候选报上来。")
        threading.Thread(target=slang_extract, args=(gid,), daemon=True).start()
        return
    if not arg:
        _emit(gid, "用法：黑话 确认 词 意思 / 黑话 拒绝 词 / 黑话 删除 词 / 黑话 学习")
        return
    if act == "确认":
        if not privileged:
            _emit(gid, "确认黑话得管理员来。")
            return
        parts = re.split(r"\s+", arg, 1)
        term, meaning = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if not meaning:
            meaning = ((terms.get(term) or {}).get("meaning") or "").strip()
        if not meaning:
            _emit(gid, "「%s」还没有解释，写成：黑话 确认 %s 是什么意思" % (term, term))
            return
        slang_learn(term, meaning, "confirmed", "管理员确认")
        _emit(gid, "记下了：%s = %s" % (term, meaning[:60]))
    elif act in ("拒绝", "删除"):
        if not privileged:
            _emit(gid, "这个得管理员来。")
            return
        with _slang_lock:
            hit = [k for k in SLANG["terms"] if k == arg or arg in k]
            for k in hit:
                if act == "删除":
                    SLANG["terms"].pop(k, None)
                else:
                    SLANG["terms"][k]["status"] = "rejected"
            save_slang()
        _emit(gid, ("删掉了 " if act == "删除" else "不采纳 ") + ("、".join(hit) or "没找到这个词"))


def slang_extract(gid=None, report=True):
    """让模型从最近群聊里挑看不懂的词，进 pending 等管理员确认。"""
    cfg = _cfg()
    gid = gid or (cfg.get("groups") or [None])[0]
    sc = cfg.get("slang", {})
    if not _context or not _chat:
        log.warning("黑话提取跳过：宿主未注入 context/chat 钩子")
        return
    blk = _context(gid, skip=int(sc.get("window", 40)))
    if not blk:
        return
    with _slang_lock:
        known = "、".join(list(SLANG["terms"])[:40]) or "（空）"
    msgs = [{"role": "system", "content":
             "你在帮一个 QQ 群机器人整理群里的黑话。从聊天记录里挑出【网络新词/梗/圈内黑话】而且不在已知列表里的词，"
             "每个给一句不超过40字的解释。只输出 JSON 数组，形如 [{\"term\":\"xx\",\"meaning\":\"xx\"}]，没有就输出 []。"},
            {"role": "user", "content": "已知：%s\n\n聊天记录：\n%s" % (known, blk)}]
    try:
        _, ans = _chat(msgs)
    except Exception as e:
        log.warning("黑话提取失败: %s", e)
        return
    m = re.search(r"\[.*\]", ans, re.S)
    if not m:
        return
    try:
        items = json.loads(m.group(0))
    except Exception:
        return
    added = []
    for it in items if isinstance(items, list) else []:
        term = str((it or {}).get("term") or "").strip()
        if not term or term in SLANG["terms"]:
            continue
        slang_learn(term, str((it or {}).get("meaning") or "").strip(), "pending", "群聊提取")
        added.append(term)
    if gid and report:
        _emit(gid, ("疑似黑话：%s（管理员发「黑话 确认 词 意思」才生效）" % "、".join(added[:6]))
              if added else "这轮没挑出新黑话")
    elif added:
        log.info("黑话候选待确认: %s", "、".join(added[:8]))


def maybe_slang_extract(gid):
    """攒够聊天记录就悄悄提一批黑话候选（进 pending，等管理员确认）。"""
    sc = _cfg().get("slang", {})
    if not (sc.get("enabled", True) and sc.get("auto_extract", True)):
        return
    need = int(sc.get("extract_min_messages", 30))
    with _slang_lock:
        _slang_state["since"] += 1
        if _slang_state["since"] < need:
            return
        _slang_state["since"] = 0
        if _slang_state["running"] or time.time() - _slang_state["last"] < float(
                sc.get("extract_every_sec", 1800)):
            return
        _slang_state["running"] = True
        _slang_state["last"] = time.time()

    def run():
        try:
            slang_extract(gid, report=False)
        finally:
            with _slang_lock:
                _slang_state["running"] = False

    threading.Thread(target=run, daemon=True).start()
