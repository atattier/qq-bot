"""Reflection：从真实对话里提炼「观察 / 看法 / 未完成事项 / 后续话题」。

第二阶段（2026-10-26）。设计见 `docs/reflection-design.md`，本文件是它的实现。

三条铁律（都对应主人给的验收项）：
  1. **人格隔离是硬约束**：每条记录带 `persona`，所有查询同时校验 `persona` + `gid`。
     历史 turns 没有 persona（NULL）的一律跳过，**不默认归给当前人格**。
  2. **事实必须能验证**：`basis=fact` 要求 `quote` 是原文**逐字子串**，且不允许
     用一句短话扩写出长结论（数字不能凭空出现、内容不能超过引文的 4 倍）。
     验不过就降级 `inference`，没有 refs 的直接丢。
  3. **本模块绝不发消息**：只产出候选，发不发由现有主动发言链路决定。
     `grep -n "send_" qbot_reflect.py` 必须为空 —— 这是验收项。

去重靠数据库唯一索引 + `ON CONFLICT DO UPDATE` 单语句完成，**不做「先查再插」**
（那样两个线程会同时插进去）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("qqbot")

KINDS = ("observation", "opinion", "open_loop", "follow_up")
BASES = ("fact", "inference", "opinion")
# 非终态（还能出现在候选里）
ALIVE = ("open", "in_progress", "blocked")
TERMINAL = ("resolved", "dismissed")

_DB: Path | None = None
_CFG: dict = {}
_LOCK = threading.Lock()
_ensured = False
_busy = {"on": False}

# 一天一次统计（每日次数上限）
STAT_SQL = """CREATE TABLE IF NOT EXISTS reflect_stat(
    day TEXT PRIMARY KEY, runs INTEGER DEFAULT 0)"""


# ---------------------------------------------------------------- 基础设施
def bind(db_path, cfg=None):
    """由 bot.py 启动时调用。db_path 与 qbot_memory 用**同一个库文件**。"""
    global _DB, _CFG
    if db_path:
        _DB = Path(db_path)
    if cfg is not None:
        _CFG = cfg
    ensure()


def cfg():
    return _CFG or {}


def _get(k, default):
    v = cfg().get(k)
    return default if v is None else v


@contextmanager
def _conn():
    """每次现开现关（和 qbot_memory 同一套路子）。

    WAL + busy_timeout：两个模块（bot 主线程写 turns、反射线程写 reflect_*）
    并发时不会立刻抛 database is locked。
    """
    if not _DB:
        raise RuntimeError("qbot_reflect 还没 bind(db_path=...)")
    c = sqlite3.connect(str(_DB), timeout=10)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=8000")
        c.execute("PRAGMA synchronous=NORMAL")
        yield c
    finally:
        c.close()


def ensure():
    """建表（幂等）。只建自己的表，不动 qbot_memory 的。"""
    global _ensured
    if _ensured or not _DB:
        return
    try:
        with _conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS reflect_item(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                persona TEXT NOT NULL,
                gid TEXT NOT NULL,
                src TEXT,
                topic TEXT,
                content TEXT NOT NULL,
                detail TEXT,
                basis TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                status TEXT NOT NULL DEFAULT 'open',
                refs TEXT,
                dedup TEXT NOT NULL,
                weight REAL DEFAULT 0.5,
                created REAL, updated REAL,
                last_used REAL, use_count INTEGER DEFAULT 0,
                mentions INTEGER DEFAULT 0, no_reply INTEGER DEFAULT 0)""")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_item_dedup "
                      "ON reflect_item(dedup)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_item_lookup "
                      "ON reflect_item(persona, gid, kind, status)")
            c.execute("""CREATE TABLE IF NOT EXISTS reflect_cursor(
                src TEXT PRIMARY KEY, persona TEXT,
                last_turn_id INTEGER DEFAULT 0, last_run REAL DEFAULT 0,
                runs INTEGER DEFAULT 0, fails INTEGER DEFAULT 0)""")
            c.execute(STAT_SQL)
            c.commit()
        _ensured = True
        log.info("Reflection 表已就绪（reflect_item / reflect_cursor / reflect_stat）")
    except Exception as e:
        log.warning("Reflection 建表失败：%s", e)


# ---------------------------------------------------------------- 小工具
def _norm_topic(t):
    t = re.sub(r"[\s，。、,.;；:：!！?？\"'“”‘’()（）\[\]【】]+", "", str(t or ""))
    return t.lower()[:24]


def _norm_text(t):
    return re.sub(r"\s+", "", str(t or "")).lower()


def _item_id(persona, gid, kind, dedup):
    raw = "%s|%s|%s|%s" % (persona, gid, kind, dedup)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _nums(s):
    return set(re.findall(r"\d+(?:\.\d+)?", str(s or "")))


# ---------------------------------------------------------------- 读取增量
def fetch_turns(src, after_id=0, persona="", limit=40):
    """取某会话在 after_id 之后的新 turns。

    ⚠️ 只取 `persona` 与本轮**完全一致**的行：
       - 老记录（persona 为 NULL）**跳过** —— 无法可信判定归属
       - 换了人格之后的新记录 persona 变了，也跳过 —— 那些是别人格的经历
    这正是主人要求的「无法确定归属就跳过，不默认归给当前人格」。
    """
    if not _DB:
        return []
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT id, ts, who, qq, text, reply FROM turns "
                "WHERE src=? AND id>? AND persona=? AND text<>'' "
                "ORDER BY id ASC LIMIT ?",
                (str(src), int(after_id), str(persona), int(limit))).fetchall()
    except Exception as e:
        log.warning("读 turns 失败：%s", e)
        return []
    return [{"id": r[0], "ts": r[1], "who": r[2], "qq": r[3],
             "text": r[4], "reply": r[5]} for r in rows]


def cursor_get(src, persona=""):
    try:
        with _conn() as c:
            r = c.execute("SELECT last_turn_id, last_run, runs, fails, persona "
                          "FROM reflect_cursor WHERE src=?", (str(src),)).fetchone()
    except Exception:
        return {"last_turn_id": 0, "last_run": 0.0, "runs": 0, "fails": 0, "persona": ""}
    if not r:
        return {"last_turn_id": 0, "last_run": 0.0, "runs": 0, "fails": 0, "persona": ""}
    return {"last_turn_id": r[0] or 0, "last_run": r[1] or 0.0,
            "runs": r[2] or 0, "fails": r[3] or 0, "persona": r[4] or ""}


# ---------------------------------------------------------------- 预筛（不调模型）
_JUNK = re.compile(r"^\[[^\]]*\]$|^[\s。，、,.!！?？~～]+$")


def prescreen(turns, max_input=40):
    """本地先筛一遍，省 token。返回 (留下的, 丢弃统计)。

    丢：纯占位符/纯标点/太短/与上一条几乎重复。
    加分：带问号、含"以后/下次/改天/还没"、长度适中。
    """
    keep, drop = [], {"junk": 0, "short": 0, "dup": 0}
    seen = []
    for t in turns:
        tx = str(t.get("text") or "").strip()
        if not tx or _JUNK.match(tx):
            drop["junk"] += 1
            continue
        if len(tx) < 4:
            drop["short"] += 1
            continue
        n = _norm_text(tx)
        if any(n == s or (len(n) > 8 and n in s) for s in seen[-6:]):
            drop["dup"] += 1
            continue
        seen.append(n)
        score = 0.0
        if "?" in tx or "？" in tx:
            score += 0.4
        if re.search(r"(以后|下次|改天|回头|还没|没搞|没弄|再说|到时候|明天|晚上)", tx):
            score += 0.5
        if 8 <= len(tx) <= 120:
            score += 0.3
        t["_score"] = score
        keep.append(t)
    keep.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return keep[:int(max_input)], drop


# ---------------------------------------------------------------- 提示词
SYS = """你在帮一个 QQ 群聊机器人做「事后反思」。给你一段真实聊天记录，你要提炼出**少量、扎实**的东西。

只输出一个 JSON 对象，不要 Markdown、不要解释：
{"items":[
  {"kind":"observation","topic":"2~6字话题词","content":"一句话","basis":"fact","confidence":0.8,
   "refs":[{"turn_id":12,"quote":"原话片段"}],"weight":0.5},
  {"kind":"opinion","topic":"...","content":"...","basis":"opinion","confidence":0.6,
   "refs":[{"turn_id":12,"quote":"..."}],"weight":0.4},
  {"kind":"open_loop","topic":"...","content":"...","basis":"fact","confidence":0.7,
   "refs":[{"turn_id":12,"quote":"..."}],"weight":0.7},
  {"kind":"follow_up","topic":"...","content":"...","basis":"inference","confidence":0.5,
   "refs":[{"turn_id":12,"quote":"..."}],"weight":0.4}
]}

四种 kind：
- observation：群里发生/被提到的事实性观察
- opinion：**你自己的看法**（可以有立场、可以不喜欢）
- open_loop：说好要做但还没做的事、悬而未决的问题
- follow_up：以后值得接着聊的话题

basis 必须诚实选：
- fact：聊天里**明确说过**的。此时 refs.quote 必须从原文里**逐字复制**一段，不许改写。
- inference：你从上下文推测的（"他大概是想…"）
- opinion：纯主观评价

硬规矩：
1. 每条 content ≤ 40 字，说人话，别写"用户表示…"这种报告腔。
2. refs 必须指向给出的 turn_id；quote 只能从那条原文里复制，**不许自己编**。
3. **不许把"你做过什么"写成事实**（比如"我今天看了一个视频"）—— 你只在群里聊天，
   没有能力去看视频。这类一律不要产出。
4. 宁可少产出（0~6 条），也不要凑数。没内容就输出 {"items":[]}。
5. 同一个话题只出一条，别重复。
6. topic 用来去重，要具体（「量化模型」好过「技术」）。"""


def build_user_text(turns, persona=""):
    lines = []
    for t in turns:
        who = str(t.get("who") or "某人")[:12]
        tx = str(t.get("text") or "")[:200]
        lines.append("[%d] %s: %s" % (t["id"], who, tx))
    head = "当前人格：%s\n\n最近聊天记录：\n" % (persona or "未命名")
    return head + "\n".join(lines) + "\n\n按要求输出 JSON。"


# ---------------------------------------------------------------- 校验
def verify_items(raw_items, turns, persona, gid, src):
    """来源校验 + 降级。返回 (通过的条目, 统计)。

    这是「不把编造的内容写成事实」的唯一闸门，规则见模块 docstring 第 2 条。
    """
    by_id = {int(t["id"]): t for t in turns}
    out, stat = [], {"no_ref": 0, "bad_ref": 0, "bad_quote": 0, "down": 0, "over": 0}
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip()
        if kind not in KINDS:
            continue
        content = str(it.get("content") or "").strip()
        if not (4 <= len(content) <= 120):
            continue
        topic = str(it.get("topic") or "").strip() or content[:6]
        basis = str(it.get("basis") or "inference").strip()
        if basis not in BASES:
            basis = "inference"
        refs, quotes = [], []
        for r in (it.get("refs") or [])[:3]:
            try:
                tid = int(r.get("turn_id"))
            except (TypeError, ValueError):
                continue
            t = by_id.get(tid)
            if not t:                      # 指向不存在的轮次 → 这条引用作废
                stat["bad_ref"] += 1
                continue
            q = str(r.get("quote") or "").strip()
            if not q:
                continue
            # quote 必须是该轮原文的逐字子串（去空白后比较，容忍空格差异）
            if _norm_text(q) not in _norm_text(t.get("text")):
                stat["bad_quote"] += 1
                continue
            refs.append({"turn_id": tid, "who": t.get("who") or "",
                         "ts": t.get("ts"), "quote": q})
            quotes.append(q)
        if not refs:
            stat["no_ref"] += 1
            continue                   # 没有任何可信引用 → 整条丢弃
        # fact 的额外门槛：不许用一句短话扩写出长结论
        if basis == "fact":
            joined = "".join(quotes)
            too_long = len(content) > max(40, len(joined) * 4)
            new_nums = _nums(content) - _nums(joined)
            if too_long or new_nums:
                stat["over"] += 1
                basis = "inference"     # 降级，不丢（观察本身可能仍然有用）
                stat["down"] += 1
        conf = it.get("confidence")
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.5
        if basis == "inference":
            conf = min(conf, 0.5)       # 推测不许给高置信
        w = it.get("weight")
        try:
            w = max(0.0, min(1.0, float(w)))
        except (TypeError, ValueError):
            w = 0.5
        dedup = "|".join([kind, str(persona), str(gid), _norm_topic(topic)])
        out.append({
            "id": _item_id(persona, gid, kind, dedup),
            "kind": kind, "persona": str(persona), "gid": str(gid), "src": str(src),
            "topic": topic[:24], "content": content[:200],
            "detail": str(it.get("detail") or "")[:400],
            "basis": basis, "confidence": conf, "status": "open",
            "refs": json.dumps(refs, ensure_ascii=False), "dedup": dedup,
            "weight": w, "created": time.time(), "updated": time.time(),
        })
    return out, stat


# ---------------------------------------------------------------- 入库（幂等）
UPSERT = """INSERT INTO reflect_item
    (id,kind,persona,gid,src,topic,content,detail,basis,confidence,status,
     refs,dedup,weight,created,updated)
    VALUES(:id,:kind,:persona,:gid,:src,:topic,:content,:detail,:basis,:confidence,
           :status,:refs,:dedup,:weight,:created,:updated)
    ON CONFLICT(dedup) DO UPDATE SET
      content    = CASE WHEN length(excluded.content) > length(reflect_item.content)
                        THEN excluded.content ELSE reflect_item.content END,
      confidence = MAX(reflect_item.confidence, excluded.confidence),
      weight     = MAX(reflect_item.weight, excluded.weight),
      refs       = excluded.refs,
      basis      = CASE WHEN reflect_item.basis='fact' THEN 'fact' ELSE excluded.basis END,
      updated    = excluded.updated
"""


def upsert_items(items):
    """单语句 upsert —— 靠唯一索引保证幂等，不做「先查再插」。返回新增/更新数。"""
    if not items:
        return 0
    n = 0
    with _conn() as c:
        for it in items:
            c.execute(UPSERT, it)
            n += 1
        c.commit()
    return n


# ---------------------------------------------------------------- 生命周期
def set_status(item_id, status, persona, gid):
    """改状态。**必须带 persona + gid**，防止跨人格/跨群误改（验收项 6）。"""
    if status not in ("open", "in_progress", "blocked", "resolved", "dismissed"):
        return False
    with _conn() as c:
        cur = c.execute("UPDATE reflect_item SET status=?, updated=? "
                        "WHERE id=? AND persona=? AND gid=?",
                        (status, time.time(), item_id, str(persona), str(gid)))
        c.commit()
        return cur.rowcount > 0


def mark_used(item_id, persona, gid):
    """被主动发言用了一次：计数 + 上次时间 + open→in_progress。"""
    with _conn() as c:
        c.execute("UPDATE reflect_item SET use_count=use_count+1, mentions=mentions+1, "
                  "last_used=?, updated=?, "
                  "status=CASE WHEN status='open' THEN 'in_progress' ELSE status END "
                  "WHERE id=? AND persona=? AND gid=?",
                  (time.time(), time.time(), item_id, str(persona), str(gid)))
        c.commit()


def note_no_reply(item_id, persona, gid):
    """提了没人接：累加；满 3 次自动 dismissed（别一直烦人）。"""
    with _conn() as c:
        c.execute("UPDATE reflect_item SET no_reply=no_reply+1, updated=? "
                  "WHERE id=? AND persona=? AND gid=?", (time.time(), item_id,
                                                         str(persona), str(gid)))
        c.execute("UPDATE reflect_item SET status='dismissed' "
                  "WHERE id=? AND persona=? AND gid=? AND no_reply>=3 AND status<>'resolved'",
                  (item_id, str(persona), str(gid)))
        c.commit()


# ---------------------------------------------------------------- 候选
def candidates(gid, persona, limit=5, kinds=("open_loop", "follow_up")):
    """给主动发言用的候选。

    ⚠️ 三个过滤同时生效：persona、gid、非终态 —— 少一个就会串味。
    weight 按 14 天半衰期**读时衰减**（不写库，省 IO）；低于 0.15 视为休眠，不出候选，
    但**行保留**（长期未解决的有价值事项不删）。
    """
    if not _DB:
        return []
    now = time.time()
    ph = ",".join("?" * len(kinds))
    sql = ("SELECT id,kind,topic,content,basis,confidence,weight,use_count,last_used,"
           "created,updated,mentions,no_reply FROM reflect_item "
           "WHERE persona=? AND gid=? AND status IN ('open','in_progress','blocked') "
           "AND kind IN (%s) ORDER BY updated DESC LIMIT 200" % ph)
    try:
        with _conn() as c:
            rows = c.execute(sql, (str(persona), str(gid)) + tuple(kinds)).fetchall()
    except Exception as e:
        log.warning("查候选失败：%s", e)
        return []
    out = []
    for r in rows:
        # ⚠️ 列序：0 id,1 kind,2 topic,3 content,4 basis,5 confidence,6 weight,
        #    7 use_count,8 last_used,9 created,10 **updated**,11 mentions,12 no_reply
        # 衰减要按 `updated`（这个话题最后一次被提到）算，不是 created ——
        # 用 created 的话，老话题反复被提起也永远"陈旧"，反之新话题一进来就算新鲜。
        age_d = max(0.0, now - (r[10] or now)) / 86400.0
        w_eff = (r[6] or 0.5) * (0.5 ** (age_d / 14.0))
        if w_eff < 0.15:
            continue                      # 休眠：不删，只是这次不提
        rec = 0.5 ** (age_d / 3.0)        # 3 天新鲜度
        used_pen = 1.0 - min((r[7] or 0) / 3.0, 0.8)
        out.append({"id": r[0], "kind": r[1], "topic": r[2], "content": r[3],
                    "basis": r[4], "confidence": r[5], "weight": round(w_eff, 3),
                    "score": round(w_eff * (0.4 + 0.6 * rec) * used_pen, 3),
                    "use_count": r[7], "mentions": r[11], "no_reply": r[12]})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:int(limit)]


def candidates_all(persona, gid):
    """调试/验收用：返回全部（含休眠），不受 score 过滤。"""
    with _conn() as c:
        return c.execute(
            "SELECT kind,topic,status,basis,round(weight,2),use_count,no_reply "
            "FROM reflect_item WHERE persona=? AND gid=? ORDER BY updated DESC",
            (str(persona), str(gid))).fetchall()


# ---------------------------------------------------------------- 主流程
def run_once(gid, src, persona, chat_fn=None, force=False):
    """跑一次 Reflection。返回 (ok, 说明, 统计)。

    ⚠️ **游标和条目在同一个事务里提交**：要么都成，要么都不动。
       不会出现「游标前进了但数据没写进去」导致这段对话永远丢失。
    """
    if not _DB or chat_fn is None:
        return False, "未绑定", {}
    if not _get("enabled", True):
        # 双保险：巡检线程已经查过一次开关，这里再查一次 ——
        # 保证「关掉之后不写库」不依赖调用方是否守规矩（验收项：开关必须真正停副作用）
        return False, "反思已关闭", {}
    if _busy["on"]:
        return False, "上一次还在跑", {}
    _busy["on"] = True
    try:
        cur = cursor_get(src, persona)
        # 人格变了 → 游标作废重来（不同人格的经历不能混着读）
        if cur.get("persona") and cur["persona"] != str(persona):
            log.info("Reflection：%s 人格变了（%s → %s），游标重置",
                     src, cur["persona"], persona)
            # 立即持久化新人格。否则新人格暂时没有 turns 时会直接 return，
            # 下一轮仍读到旧 persona，反复重置并刷屏，新记录也可能一直被跳过。
            _commit_cursor(src, persona, 0, ok=True)
            cur = cursor_get(src, persona)
        now = time.time()
        if not force:
            if now - float(cur["last_run"] or 0) < float(_get("min_interval", 1800)):
                return False, "还没到间隔", {}
            if daily_runs() >= int(_get("daily_max", 12)):
                return False, "今天次数用完了", {}
        turns = fetch_turns(src, cur["last_turn_id"], persona,
                            int(_get("max_input", 40)))
        if not turns:
            return False, "没有新记录（或新记录不属于当前人格）", {}
        picked, drop = prescreen(turns, int(_get("max_input", 40)))
        if not picked:
            _commit_cursor(src, persona, turns[-1]["id"], ok=True)
            return True, "新记录都不值得提炼", {"drop": drop}
        t0 = time.time()
        try:
            raw = chat_fn([{"role": "system", "content": SYS},
                           {"role": "user", "content": build_user_text(picked, persona)}])
            data = _extract_json(raw)
        except Exception as e:
            _commit_cursor(src, persona, cur["last_turn_id"], ok=False)
            log.warning("Reflection 模型调用失败 src=%s：%s", src, str(e)[:120])
            return False, "模型调用失败", {"err": str(e)[:80]}
        items, vstat = verify_items(data.get("items"), picked, persona, gid, src)
        try:
            # ⚠️ 条目 + 游标同一事务
            with _conn() as c:
                for it in items:
                    c.execute(UPSERT, it)
                c.execute(
                    "INSERT INTO reflect_cursor(src,persona,last_turn_id,last_run,runs,fails) "
                    "VALUES(?,?,?,?,1,0) ON CONFLICT(src) DO UPDATE SET "
                    "persona=excluded.persona, last_turn_id=excluded.last_turn_id, "
                    "last_run=excluded.last_run, runs=reflect_cursor.runs+1, fails=0",
                    (str(src), str(persona), int(turns[-1]["id"]), time.time()))
                c.commit()
        except Exception as e:
            log.warning("Reflection 入库失败（游标未前进，下次重试）：%s", str(e)[:120])
            return False, "入库失败", {"err": str(e)[:80]}
        _bump_daily()
        log.info("Reflection src=%s 新增%d 候选%d 入库%d 用时%.1fs 丢弃%r 校验%r",
                 src, len(turns), len(picked), len(items), time.time() - t0, drop, vstat)
        return True, "完成", {"turns": len(turns), "picked": len(picked),
                              "items": len(items), "drop": drop, "verify": vstat}
    finally:
        _busy["on"] = False


def _commit_cursor(src, persona, turn_id, ok=True):
    """只推游标（没产出条目时用，比如全被筛掉/模型失败）。"""
    try:
        with _conn() as c:
            if ok:
                c.execute(
                    "INSERT INTO reflect_cursor(src,persona,last_turn_id,last_run,runs,fails) "
                    "VALUES(?,?,?,?,1,0) ON CONFLICT(src) DO UPDATE SET "
                    "persona=excluded.persona, last_turn_id=excluded.last_turn_id, "
                    "last_run=excluded.last_run, runs=reflect_cursor.runs+1, fails=0",
                    (str(src), str(persona), int(turn_id), time.time()))
            else:
                c.execute("UPDATE reflect_cursor SET fails=fails+1, last_run=? WHERE src=?",
                          (time.time(), str(src)))
            c.commit()
    except Exception as e:
        log.debug("游标更新失败：%s", e)


def _extract_json(raw):
    """从模型回复里抠 JSON（可能带 ```json 围栏或前后废话）。"""
    s = str(raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("回复里没有 JSON")
    return json.loads(s[i:j + 1])


# ---------------------------------------------------------------- 每日计数
def daily_runs():
    day = time.strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            r = c.execute("SELECT runs FROM reflect_stat WHERE day=?", (day,)).fetchone()
            return int(r[0]) if r else 0
    except Exception:
        return 0


def _bump_daily():
    day = time.strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            c.execute("INSERT INTO reflect_stat(day,runs) VALUES(?,1) "
                      "ON CONFLICT(day) DO UPDATE SET runs=reflect_stat.runs+1", (day,))
            c.commit()
    except Exception:
        pass


# ---------------------------------------------------------------- 统计（验收/网页用）
def stats(gid=None, persona=None):
    if not _DB:
        return {"total": 0, "by": [], "note": "未 bind"}
    with _conn() as c:
        q = "SELECT kind,status,COUNT(*) FROM reflect_item"
        args = []
        if gid is not None and persona is not None:
            q += " WHERE gid=? AND persona=?"
            args = [str(gid), str(persona)]
        q += " GROUP BY kind,status"
        rows = c.execute(q, args).fetchall()
        total = c.execute("SELECT COUNT(*) FROM reflect_item").fetchone()[0]
    return {"total": total, "by": [{"kind": r[0], "status": r[1], "n": r[2]} for r in rows]}


# ---------------------------------------------------------------- 网页只读接口（2026-11 阶段 2 批 2a）
def list_items(persona, gid=None, kinds=None, limit=60):
    """按人格列观点/反思明细（只读，/api/reflect_items 用）。

    🔴 修订 8（主人 2026-11-01）：persona 过滤是红线 —— 本函数只认 persona 参数，
    **不做任何「默认当前人格」的兜底**；空 persona 一律返回空集，
    宁可页面空也绝不串出别人格的观点（qbot_memory.py turns.persona 同一语义）。
    """
    if not _DB or not str(persona or "").strip():
        return []
    q = ("SELECT id,kind,persona,gid,src,topic,content,detail,basis,confidence,"
         "status,created,updated,use_count FROM reflect_item WHERE persona=?")
    args = [str(persona)]
    if gid is not None and str(gid).strip():
        q += " AND gid=?"
        args.append(str(gid))
    ks = [k for k in (kinds or []) if k in KINDS]
    if ks:
        q += " AND kind IN (%s)" % ",".join("?" * len(ks))
        args += ks
    q += " ORDER BY COALESCE(updated, created, 0) DESC LIMIT ?"
    try:
        args.append(max(1, min(int(limit or 60), 200)))
    except (TypeError, ValueError):
        args.append(60)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    cols = ["id", "kind", "persona", "gid", "src", "topic", "content", "detail",
            "basis", "confidence", "status", "created", "updated", "use_count"]
    now = time.time()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        for tk in ("created", "updated"):
            if d.get(tk):
                d[tk + "_ago_sec"] = int(now - d[tk])
        out.append(d)
    return out


def cursor_info():
    """反思游标/运行概况（只读，/api/reflect_stat 用）：每个来源跑到哪、成功失败几次。"""
    if not _DB:
        return []
    with _conn() as c:
        rows = c.execute("SELECT src,persona,last_turn_id,last_run,runs,fails "
                         "FROM reflect_cursor").fetchall()
    now = time.time()
    return [{"src": r[0], "persona": r[1], "last_turn_id": r[2],
             "last_run_ago_sec": int(now - r[3]) if r[3] else None,
             "runs": r[4], "fails": r[5]} for r in rows]
