# -*- coding: utf-8 -*-
"""长期记忆（2026-10-13 建，2026-10-19 改成「按人存」）。

第一版是「按群存」：`person(gid,qq)` / `fact(gid,qq)`。问题很明显——
同一个人在群里说过的事，私聊时就不记得了（换群更不用说）。

2026-10-19 参考 ai-companion-kit 的 Memory 改成三层，**主键都是人（QQ），
群号降级成「这句话是在哪说的」标签**：

  people   一本账：名字 / 首次最后出现 / 互动次数           ← 按 qq
  facts    画像事实：性格、习惯、他告诉过你的事              ← 按 qq（带 src 记录出处）
  turns    跨场合对话流：各个群和私聊的一来一回排成一条线     ← 全局，最多 400 轮
  person / fact（老表，留着不删）：谁在哪个群出现过 / 老画像，兼容 + 迁移用

⚠️ 项目规矩（同 qbot_slang / qbot_persona）：
**本模块不 import bot**，宿主能力靠 bind() 注入。

⚠️ 踩坑备忘：
1. sqlite3 连接别跨线程共用，每个操作现开现关（bot 是多线程的，会「database is locked」）。
2. 中文没分词库，检索用「query 切片段 + LIKE 命中计数」，片段 ≥2 字。
3. 画像会记错/过期，必须能遗忘（forget），不然会一直说错话。
4. `src` 是「哪个会话」，别用 `where` 当列名（SQL 关键字）。

2026-10-26 晚：主人问「有无很厉害的记忆插件」，看了 AstrBot 那几家（Wave Memory /
Iris / 灵笔司书），结论是**别整套搬**（都 AGPL），但它们的三个看家本领值得抄思路，
于是给这个模块加了三件事（表结构只加列，不动老数据）：

  ① **向量召回**：`embed()` 走本地 ollama（默认 bge-m3），存 float32 blob 到 `facts.vec`；
     没有模型就退回「字符二元组余弦」，一样能算相似度（只是不懂同义改写）。
     召回改成 `0.55*语义 + 0.30*关键词 + 0.15*权重` 的混合打分。
     ⚠️ 2026-10-26 23:20 起 **embed 默认关**（主人：「别给我电脑搞炸了」）：
     bge-m3 常驻要 0.66GB 显存、跟识图用的 VL 模型抢，ollama 一换模型热路径就超时。
     默认走二元组那条（纯 Python，1ms，零模型），要真语义再把 `memory.embed` 打开。
  ② **遗忘与权重**：`facts` 加 `weight`（重要度）/ `hits`（被用过几次）/ `last_hit`；
     打分里带时间衰减（半衰期默认 180 天，地板 0.25）—— 两年前的临时事自然沉底，
     不用手删；被反复用到的记忆反而更牢（有点像人的复述效应）。
  ③ **分层注入**：常驻的仍是「这个人的一本账 + 最近真对话」；**另加** `recall_block()`
     按当前这句话召回 3~5 条相关的旧记忆，带字数上限，且**只给相关的那几条**，
     不再把所有事实一股脑塞进 system。
"""
import hashlib
import json
import logging
import math
import operator
import queue
import re
import sqlite3
import struct
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot")

_LOCK = threading.Lock()
_DB = None
_CFG = {}                      # bot 传进来的整份配置（只读 memory 段）
MAX_FACTS_PER_PERSON = 120     # 单人事上限，超了删最旧的
MAX_TURNS = 400                # 跨场合对话流上限
SCHEMA_V = "2"
_ensured = False               # 进程内只做一次列补齐（PRAGMA 检查）
_fact_cols_ok = False          # facts 的新列（weight/hits/last_hit/vec）补齐标记

# —— 混合召回的打分权重（2026-10-26）——
W_SEM, W_KW, W_WT = 0.55, 0.30, 0.15
# 入围线是**实测**出来的，不是拍的（2026-10-26 拿线上 15 条真事实量的）：
#   无关句「今天好累啊」最高 0.508、「你记得我喜欢什么吗」0.521；
#   真相关「发抖音链接」0.691、「谁爱认宝宝」0.672、「出图用什么站」0.653。
#   bge-m3 的短句余弦底噪就在 0.45~0.55，所以线必须压到 0.60 以上，
#   否则每句闲聊都能召回两条不相干的旧事，模型会开始胡扯。
SEM_MIN_DENSE = 0.60
SEM_MIN_BIGRAM = 0.34          # 二元组覆盖率/余弦的入围线（没有向量时的默认路径）
POOL = 600                     # 一次最多算多少条候选（纯 Python 点积，别开太大）

# —— 向量层状态 ——
_EMB_CACHE = {}                # md5(text) -> vec（命中率极高：群里翻来覆去就那几句话）
_EMB_CACHE_MAX = 600
_EMB_DOWN_UNTIL = 0.0          # 失败退避：模型没装/服务没开时别每条消息都去撞一次
_EMB_DOWN_SEC = 300
_EMB_LAST = {"ok": None, "why": "", "model": "", "ts": 0.0, "ms": 0}
_EMB_OVERRIDE = None           # 测试注入：text -> vec | None（不走网络）
_VEC_Q = None                  # 后台补向量的队列（懒建，一个 worker）
_VEC_T = None

# 事实的初始重要度：越「值得记」的越不容易被时间冲掉
_KIND_W = {"promise": 1.5, "like": 1.4, "dislike": 1.4, "event": 1.2,
           "fact": 1.2, "note": 1.0, "chat": 0.8}

_CFG_DEFAULTS = {
    # 2026-10-26 23:20 主人说「别给我电脑搞炸了」——向量召回**默认关**。
    # bge-m3 常驻要 0.66GB 显存，跟识图用的 VL 模型抢；而且 ollama 一换模型，
    # 热路径 3 秒超时就会全灭。所以默认走「字符二元组余弦」（纯 Python，1ms，零模型），
    # 想要真语义就把 embed 打开（bge-m3 已经拉好了，拉一次 1.2GB，别再拉）。
    "embed": False,                                  # 向量召回总开关（默认关）
    "embed_model": "bge-m3",                         # 本地 ollama 的嵌入模型
    "embed_url": "http://127.0.0.1:11434",
    "embed_timeout": 3.0,                            # 单次嵌入超时（秒），别拖住回复
    "embed_keepalive": "30m",                        # 让 ollama 别卸载模型（闲置 5 分钟就会卸）
    "hybrid": True,                                  # 混合打分（关掉=退回纯关键词）
    "decay_days": 180,                               # 权重半衰期（天）
    "weight_floor": 0.25,                            # 衰减地板，老记忆不会被压成 0
    "recall_n": 4,                                   # 分层注入：每轮最多召几条
    "recall_chars": 320,                             # 分层注入：这一段的总字数硬上限
    "recall_min": 0.20,                              # 低于这个分不进注入块
    # —— 阶段 D 实验项（2026-11-04，默认关=旧线性打分逐字节不变）——
    "fusion": "linear",                              # linear | rrf（RRF 融合，对分数尺度不敏感）
    "rrf_k": 60,                                     # RRF 常数 k
    "rrf_dynamic": True,                             # RRF 模式下按本次候选分布动态取阈值
    "rrf_min_results": 4,                            # 动态阈值至少保留几条
}


# ---------------------------------------------------------------- 绑定 / 初始化
def bind(state_dir=None, cfg=None):
    """由 bot.py 启动时调用，传 state 目录（+ 整份配置，读 memory 段用）。

    `cfg` 存的是**同一份 dict 的引用**，网页上改完立刻生效，不用重启。
    """
    global _DB, _CFG, _fact_cols_ok, _EMB_CACHE, _ensured
    if isinstance(cfg, dict):
        _CFG = cfg
    _ensured = False   # 换库重入时 turns.persona 补列检查要重跑（2026-11-01 修）
    if state_dir:
        _fact_cols_ok = False      # 换了库文件，新列要重新检查
        _EMB_CACHE = {}
        _DB = Path(state_dir) / "maimem.db"
        _DB.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _LOCK, _conn() as c:
                _migrate(c)
        except Exception:
            log.warning("记忆库初始化失败", exc_info=True)
    return str(_DB) if _DB else ""


def _mcfg():
    """读 memory 段配置（键名和 config.json 里的一致，缺的用默认）。"""
    d = {}
    if isinstance(_CFG, dict):
        raw = _CFG.get("memory")
        if isinstance(raw, dict):
            d = raw
    out = dict(_CFG_DEFAULTS)
    for k, dv in _CFG_DEFAULTS.items():
        v = d.get(k)
        if v is not None and not (isinstance(v, str) and not v.strip()):
            out[k] = v
    return out


def db_path():
    return str(_DB) if _DB else ""


def _conn():
    """现开现关，别共用连接（多线程下会锁 SQLite）。"""
    if not _DB:
        raise RuntimeError("qbot_memory 还没 bind(state_dir=...)")
    c = sqlite3.connect(str(_DB), timeout=10)
    # 已经是 WAL 就别再设一遍：`PRAGMA journal_mode=WAL` 要拿写锁，
    # 后台补向量的线程正在写的时候，这里会抛 "database is locked"。
    try:
        if str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            c.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    # —— 老表（第一版，按群；留着做兼容与迁移）——
    c.execute("""CREATE TABLE IF NOT EXISTS person(
        gid TEXT, qq TEXT, name TEXT, first_seen REAL, last_seen REAL, msg_count INTEGER,
        PRIMARY KEY(gid, qq))""")
    c.execute("""CREATE TABLE IF NOT EXISTS fact(
        id INTEGER PRIMARY KEY AUTOINCREMENT, gid TEXT, qq TEXT, name TEXT,
        content TEXT, kind TEXT, ts REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_fact_gid ON fact(gid)")
    # —— 第二版：按人 ——
    c.execute("""CREATE TABLE IF NOT EXISTS people(
        qq TEXT PRIMARY KEY, name TEXT, first_seen REAL, last_seen REAL, interactions INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS facts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, qq TEXT, name TEXT, content TEXT,
        kind TEXT, ts REAL, src TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_facts_qq ON facts(qq)")
    c.execute("""CREATE TABLE IF NOT EXISTS turns(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, src TEXT, who TEXT,
        qq TEXT, text TEXT, reply TEXT)""")
    c.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    _ensure_turn_persona(c)
    _ensure_fact_cols(c)
    return c


def _ensure_turn_persona(c):
    """给 turns 补 `persona` 列（2026-10-26，Reflection 的人格隔离前提）。

    为什么要这一列：Reflection 要按人格提炼观点，而**历史记录没有归属**。
    补列之后：
      · 新记录写入时带上当时的人格
      · 老记录该列是 NULL —— 下游**必须跳过**，不能默认归给当前人格
        （否则换个「毒舌群友」就会把「小鲸」时期的话当成自己的）
    只在进程内查一次表结构，不是每次连接都查。
    """
    global _ensured
    if _ensured:
        return
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(turns)").fetchall()]
        if "persona" not in cols:
            c.execute("ALTER TABLE turns ADD COLUMN persona TEXT")
            log.info("记忆库迁移：turns 补 persona 列（老记录为 NULL，Reflection 会跳过）")
        _ensured = True
    except Exception as e:
        log.warning("turns.persona 列补齐失败（不影响主流程）: %s", e)


def _migrate(c):
    """老库升级：把 (gid,qq) 的数据汇总到按人的表里，只做一次。"""
    row = c.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if row and row[0] == SCHEMA_V:
        return
    n_p = c.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    n_f = c.execute("SELECT COUNT(*) FROM fact").fetchone()[0]
    if n_p:
        for r in c.execute("SELECT qq, MAX(name), MIN(first_seen), MAX(last_seen), SUM(msg_count) "
                           "FROM person GROUP BY qq").fetchall():
            c.execute("INSERT OR IGNORE INTO people VALUES(?,?,?,?,?)",
                      (r[0], r[1] or "", r[2] or 0, r[3] or 0, int(r[4] or 0)))
    if n_f:
        for r in c.execute("SELECT qq,name,content,kind,ts,gid FROM fact").fetchall():
            dup = c.execute("SELECT id FROM facts WHERE qq=? AND content=?",
                            (r[0], r[2])).fetchone()
            if not dup:
                c.execute("INSERT INTO facts(qq,name,content,kind,ts,src) VALUES(?,?,?,?,?,?)",
                          (r[0], r[1], r[2], r[3], r[4], r[5]))
    c.execute("INSERT OR REPLACE INTO meta VALUES('schema',?)", (SCHEMA_V,))
    c.execute("INSERT OR REPLACE INTO meta VALUES('migrated_at',?)", (str(time.time()),))
    log.info("记忆库升级到按人存：迁入 %d 人 / %d 条事实", n_p, n_f)


def _ensure_fact_cols(c):
    """给 facts 补 4 列（2026-10-26 三件套的地基）。

      weight    初始重要度（按 kind 给，承诺/喜好比随手记高）
      hits      被召回用过几次（复述效应：越用越牢）
      last_hit  上次被用的时间
      vec       这句话的向量（float32 blob；没有嵌入模型时一直是 NULL）

    **只加列、不动老数据**：老记录 weight 取默认 1.0，vec 为 NULL，
    召回时自动退回「字符二元组余弦」，不会因为没向量就查不到。
    和 turns.persona 一样，进程内只查一次表结构。
    """
    global _fact_cols_ok
    if _fact_cols_ok:
        return
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(facts)").fetchall()]
        added = []
        for name, ddl in (("weight", "REAL DEFAULT 1.0"),
                          ("hits", "INTEGER DEFAULT 0"),
                          ("last_hit", "REAL DEFAULT 0"),
                          ("vec", "BLOB")):
            if name not in cols:
                c.execute("ALTER TABLE facts ADD COLUMN %s %s" % (name, ddl))
                added.append(name)
        if added:
            log.info("记忆库迁移：facts 补列 %s（老记录 weight=1.0、vec 待补）",
                     "/".join(added))
        _fact_cols_ok = True
    except Exception as e:
        log.warning("facts 新列补齐失败（降级为纯关键词检索）: %s", e)


# ---------------------------------------------------------------- 向量层（embed）
def _opener():
    """绕开系统代理（Clash 之类会把 127.0.0.1 也劫走 → WinError 10061）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _emb_ok(model, ms=0):
    _EMB_LAST.update({"ok": True, "why": "", "model": model,
                      "ts": time.time(), "ms": int(ms)})


def _emb_fail(model, why):
    """失败就退避 5 分钟 —— 模型没拉下来/ollama 没开时，别每条消息都白等 3 秒。"""
    global _EMB_DOWN_UNTIL
    first = _EMB_LAST.get("ok") is not False
    _EMB_DOWN_UNTIL = time.time() + _EMB_DOWN_SEC
    _EMB_LAST.update({"ok": False, "why": str(why)[:120], "model": model,
                      "ts": time.time(), "ms": 0})
    if first:
        log.info("向量召回暂不可用（%s），先用字符二元组顶着；5 分钟后再试", why)


def _embed_remote(text, timeout=None, force=False):
    cfg = _mcfg()
    if not cfg.get("embed"):
        return None
    if not force and time.time() < _EMB_DOWN_UNTIL:
        return None
    url = str(cfg.get("embed_url") or "").rstrip("/")
    model = str(cfg.get("embed_model") or "bge-m3")
    if not url:
        return None
    try:
        to = float(timeout or cfg.get("embed_timeout") or 3.0)
    except Exception:
        to = 3.0
    body = json.dumps({"model": model, "input": text,
                       # 关键：不留住模型的话，ollama 闲置 5 分钟就把它踢出内存，
                       # 下一次调用要冷启动 50 秒（实测 51.4s）——热路径 3 秒超时必然全灭。
                       "keep_alive": str(cfg.get("embed_keepalive") or "30m")},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url + "/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with _opener().open(req, timeout=to) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        v = None
        if isinstance(data.get("embeddings"), list) and data["embeddings"]:
            v = data["embeddings"][0]
        elif isinstance(data.get("embedding"), list):
            v = data["embedding"]          # 老接口 /api/embeddings 的返回
        if isinstance(v, list) and len(v) >= 16:
            _emb_ok(model, (time.time() - t0) * 1000)
            return [float(x) for x in v]
        _emb_fail(model, "返回里没有向量（模型可能不支持嵌入）")
    except Exception as e:
        _emb_fail(model, "%s: %s" % (type(e).__name__, e))
    return None


def embed(text, timeout=None, force=False):
    """一句话 -> 向量；拿不到就 None（调用方退回字符二元组，不算失败）。

    带 md5 缓存：群里翻来覆去就那几句话，第二条同样的消息直接命中。
    `timeout` / `force` 只给后台任务用（补向量要能等冷启动、要能突破退避）。
    """
    text = str(text or "").strip()[:300]
    if not text:
        return None
    h = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()
    if h in _EMB_CACHE:
        return _EMB_CACHE[h]
    if _EMB_OVERRIDE is not None:
        v = _EMB_OVERRIDE(text)
    else:
        v = _embed_remote(text, timeout=timeout, force=force)
    if v:
        if len(_EMB_CACHE) >= _EMB_CACHE_MAX:
            _EMB_CACHE.clear()
        _EMB_CACHE[h] = v
    return v


def _blob(vec):
    return struct.pack("<%df" % len(vec), *vec)


def _unblob(b):
    if not b:
        return None
    try:
        n = len(b) // 4
        return list(struct.unpack("<%df" % n, b[:n * 4]))
    except Exception:
        return None


def _bigrams(text):
    """中文没分词库时的「粗语义」：字符二元组词袋。

    为什么二元组够用：「考研」vs「在准备考研」共享 bigram「考研」→ 余弦 0.5；
    换个说法（「研究生考试」）就抓不到了 —— 这正是要向量模型补的那一块。
    """
    s = re.sub(r"[^\w\u4e00-\u9fa5]+", "", str(text or "").lower())
    d = {}
    if len(s) == 1:
        d[s] = 1
    for i in range(len(s) - 1):
        g = s[i:i + 2]
        d[g] = d.get(g, 0) + 1
    return d


def _cos_dense(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(map(operator.mul, a, a)))
    nb = math.sqrt(sum(map(operator.mul, b, b)))
    if na <= 0 or nb <= 0:
        return 0.0
    return sum(map(operator.mul, a, b)) / (na * nb)


def _cos_map(a, b):
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _cover_map(q, b):
    """「这句话的内容有多大比例出现在那条事实里」——多元集合覆盖率。

    为什么要它：余弦会被长度稀释。实测「发抖音链接会怎么样」vs 那条
    「发抖音等短视频链接时，希望小鲸把解析后的视频链接发回来」共享 3 个二元组，
    余弦只有 0.237（因为事实那 20 个二元组把分母撑大了），直接被阈值刷掉；
    但覆盖率是 3/8 = 0.375 —— 「你问的这件事，那条记忆里确实在说」。
    记忆召回天然是「短问句 ⊂ 长事实」，所以这条比余弦更对路。
    """
    if not q or not b:
        return 0.0
    tot = sum(q.values())
    if tot <= 0:
        return 0.0
    hit = sum(min(v, b.get(k, 0)) for k, v in q.items())
    return hit / float(tot)


def _decay(ts, cfg=None):
    """时间衰减：半衰期到了打五折，地板 0.25 —— 老事不会消失，只是排后面。"""
    cfg = cfg or _mcfg()
    try:
        hl = max(1.0, float(cfg.get("decay_days") or 180)) * 86400.0
        floor = max(0.0, min(1.0, float(cfg.get("weight_floor") or 0.25)))
    except Exception:
        hl, floor = 180 * 86400.0, 0.25
    age = max(0.0, time.time() - float(ts or 0))
    return max(floor, 0.5 ** (age / hl))


def _vec_worker():
    """后台补向量：写记忆的人不用等模型（一条 100~300ms，别卡住回复）。"""
    while True:
        item = _VEC_Q.get()
        if item is None:
            _VEC_Q.task_done()
            continue
        fid, content = item
        try:
            v = embed(content)
            if v and _DB:
                with _LOCK, _conn() as c:
                    c.execute("UPDATE facts SET vec=? WHERE id=? AND vec IS NULL",
                              (sqlite3.Binary(_blob(v)), int(fid)))
        except Exception:
            log.debug("补向量失败 id=%s", fid, exc_info=True)
        finally:
            try:
                _VEC_Q.task_done()
            except Exception:
                pass
        time.sleep(0.05)


def _vec_enqueue(fid, content):
    global _VEC_Q, _VEC_T
    if not _mcfg().get("embed"):
        return
    try:
        if _VEC_Q is None:
            _VEC_Q = queue.Queue(maxsize=1000)
        if _VEC_T is None or not _VEC_T.is_alive():
            _VEC_T = threading.Thread(target=_vec_worker, name="mem-vec", daemon=True)
            _VEC_T.start()
        _VEC_Q.put_nowait((int(fid), str(content)))
    except Exception:
        pass          # 队列满了就这一条不补，下次 backfill 再说


def backfill(limit=8):
    """给还没有向量的老记录补向量（由 bot 的定时循环在**后台线程**里慢慢磨）。

    为什么不在 bind() 里全量补：库里有几千条时，启动会被 ollama 拖住几十秒，
    而启动慢的代价是「群消息漏接」。所以改成每轮补几条。
    为什么这里要 `force=True` + 长超时：第一次调用可能是**冷启动**（实测 51 秒），
    这条路径在后台线程里，等得起；热路径（回话时）等不起，那边只给 3 秒。
    """
    if not _DB or not _mcfg().get("embed"):
        return 0
    try:
        with _conn() as c:
            rows = c.execute("SELECT id,content FROM facts WHERE vec IS NULL "
                             "ORDER BY ts DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    except Exception as e:
        log.debug("补向量：读库失败，下一轮再试（%s）", e)
        return 0
    if not rows:
        _keepalive()          # 没有要补的，也发一条空转请求把模型留在内存里
        return 0
    n = 0
    for fid, content in rows:
        v = embed(content, timeout=60, force=True)
        if not v:
            break                 # 真拿不到就别硬试剩下的
        try:
            with _LOCK, _conn() as c:
                c.execute("UPDATE facts SET vec=? WHERE id=?",
                          (sqlite3.Binary(_blob(v)), int(fid)))
            n += 1
        except Exception:
            break
    return n


def _keepalive():
    """空转一下，让 ollama 别把 bge-m3 踢出内存（闲置 5 分钟就卸载）。"""
    try:
        embed("嗯", timeout=30, force=True)
    except Exception:
        pass


def flush_vec(timeout=10.0):
    """等后台补向量队列排空（自测/关停前用，别在回话路径上调）。"""
    if _VEC_Q is None:
        return True
    t0 = time.time()
    while time.time() - t0 < float(timeout):
        try:
            if _VEC_Q.unfinished_tasks == 0:
                return True
        except Exception:
            return False
        time.sleep(0.02)
    return False


def warmup():
    """启动时预热（后台线程调用）：把模型加载进来，第一条真消息就不用等冷启动。"""
    if not _DB or not _mcfg().get("embed"):
        return False
    return bool(embed("预热", timeout=120, force=True))


def embed_status():
    """给 /admin 和自测看的：向量层到底好不好使、补了多少条。"""
    cfg = _mcfg()
    n_all = n_vec = 0
    if _DB:
        try:
            with _conn() as c:
                n_all = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                n_vec = c.execute("SELECT COUNT(*) FROM facts WHERE vec IS NOT NULL"
                                  ).fetchone()[0]
        except Exception:
            pass
    return {"enabled": bool(cfg.get("embed")), "model": cfg.get("embed_model"),
            "ok": _EMB_LAST.get("ok"), "why": _EMB_LAST.get("why") or "",
            "ms": _EMB_LAST.get("ms") or 0, "vec": n_vec, "facts": n_all,
            "cache": len(_EMB_CACHE),
            "down_for": max(0, int(_EMB_DOWN_UNTIL - time.time()))}


# ---------------------------------------------------------------- 自动层：记人
def note_person(gid, qq, name=""):
    """bot 每收到一条消息就调一次：群内出现记录 + 全局账本各写一次。"""
    if not _DB or not qq:
        return
    gid, qq, name = str(gid), str(qq), (name or "")[:40]
    now = time.time()
    try:
        with _LOCK, _conn() as c:
            row = c.execute("SELECT first_seen,msg_count FROM person WHERE gid=? AND qq=?",
                            (gid, qq)).fetchone()
            if row:
                c.execute("UPDATE person SET name=COALESCE(NULLIF(?,''),name), last_seen=?, "
                          "msg_count=msg_count+1 WHERE gid=? AND qq=?", (name, now, gid, qq))
            else:
                c.execute("INSERT INTO person VALUES(?,?,?,?,?,1)", (gid, qq, name, now, now))
            p = c.execute("SELECT interactions FROM people WHERE qq=?", (qq,)).fetchone()
            if p:
                c.execute("UPDATE people SET name=COALESCE(NULLIF(?,''),name), last_seen=?, "
                          "interactions=interactions+1 WHERE qq=?", (name, now, qq))
            else:
                c.execute("INSERT INTO people VALUES(?,?,?,?,1)", (qq, name, now, now))
    except Exception:
        pass   # 记忆写失败绝不能影响主流程


def people(gid=None, limit=12):
    """列人。给了 gid 就列这个群里出现过的；不给就列全局账本（按互动次数倒序）。"""
    if not _DB:
        return []
    try:
        with _conn() as c:
            if gid:
                rows = c.execute("SELECT qq,name,first_seen,last_seen,msg_count FROM person "
                                 "WHERE gid=? ORDER BY msg_count DESC LIMIT ?",
                                 (str(gid), limit)).fetchall()
            else:
                rows = c.execute("SELECT qq,name,first_seen,last_seen,interactions FROM people "
                                 "ORDER BY interactions DESC LIMIT ?", (limit,)).fetchall()
        return [{"qq": r[0], "name": r[1], "first_seen": r[2], "last_seen": r[3],
                 "count": r[4]} for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------- 自主层：画像事实
def remember_fact(gid, qq, content, name="", kind="note"):
    """agent 大脑调 qq_remember 时走这里。**按人去重**（同一个人说过的话只留一条）。

    2026-10-26：写入时顺手定「初始重要度」（按 kind），并把这句话丢给后台补向量 ——
    **不在写路径上等模型**，因为 qq_remember 是在群里等回复的时候调的。
    """
    if not _DB or not content:
        return False
    qq, content = str(qq or ""), content.strip()[:300]
    if not content:
        return False
    kind = (kind or "note")[:20]
    now = time.time()
    new_id = 0
    try:
        with _LOCK, _conn() as c:
            if qq:
                dup = c.execute("SELECT id FROM facts WHERE qq=? AND content=?",
                                (qq, content)).fetchone()
            else:
                dup = c.execute("SELECT id FROM facts WHERE content=? AND src=?",
                                (content, str(gid))).fetchone()
            if dup:
                # 又提了一遍 = 这条还挺重要：加权 + 记一次「被确认」
                c.execute("UPDATE facts SET ts=?, weight=MIN(2.0, "
                          "COALESCE(weight,1.0)+0.2), hits=COALESCE(hits,0)+1, "
                          "last_hit=? WHERE id=?", (now, now, dup[0]))
                return False
            cur = c.execute("INSERT INTO facts(qq,name,content,kind,ts,src,weight,hits,last_hit)"
                            " VALUES(?,?,?,?,?,?,?,0,0)",
                            (qq, (name or "")[:40], content, kind, now, str(gid),
                             _KIND_W.get(kind, 1.0)))
            new_id = cur.lastrowid or 0
            # 老表也写一份（别的工具还在读它）
            c.execute("INSERT INTO fact(gid,qq,name,content,kind,ts) VALUES(?,?,?,?,?,?)",
                      (str(gid), qq, (name or "")[:40], content, kind, now))
            if qq:
                n = c.execute("SELECT COUNT(*) FROM facts WHERE qq=?", (qq,)).fetchone()[0]
                if n > MAX_FACTS_PER_PERSON:
                    c.execute("DELETE FROM facts WHERE id IN (SELECT id FROM facts WHERE qq=? "
                              "ORDER BY ts ASC LIMIT ?)", (qq, n - MAX_FACTS_PER_PERSON))
    except Exception:
        log.warning("写记忆失败", exc_info=True)
        return False
    if new_id:
        _vec_enqueue(new_id, content)     # 事务提交之后再排队，别让 worker 撞锁
    return True


def _fact_weight(w, ts, hits=0, cfg=None):
    """一条事实的「份量」= 初始重要度 × 时间衰减 × 复述加成，夹在 0~1。

    复述加成故意做得很小（最多 +30%）：否则「被召回过一次」的记忆会自我强化，
    越用越占前排，最后变成只有那几条老事能被想起来（富者愈富）。
    """
    try:
        w = float(w if w is not None else 1.0)
    except Exception:
        w = 1.0
    try:
        h = float(hits or 0)
    except Exception:
        h = 0.0
    boost = 1.0 + min(0.30, 0.05 * h)
    return max(0.0, min(1.0, w * _decay(ts, cfg) * boost))


def _rrf_rerank(scored, cfg):
    """RRF 融合 + 动态阈值（纯函数，供阶段 D 实验；默认不启用）。

    scored: [(total, sem, kw_norm, weight, row, why), ...]
    返回同样的结构，total 归一化到 0~1，供 recall_block 的 recall_min 继续生效。
    """
    if not scored:
        return scored
    k = max(1, int(cfg.get("rrf_k") or 60))
    n = len(scored)
    ranks = [{}, {}, {}]
    for idx in range(3):
        order = sorted(scored, key=lambda x: (-x[1 + idx], -x[4][5]))
        for rank, item in enumerate(order, 1):
            ranks[idx][item[4][0]] = rank
    fused = []
    for total, sem, kwn, w, row, why in scored:
        v = (W_SEM / (k + ranks[0].get(row[0], n + 1))
             + W_KW / (k + ranks[1].get(row[0], n + 1))
             + W_WT / (k + ranks[2].get(row[0], n + 1)))
        fused.append((v, sem, kwn, w, row, why))
    mx = max(x[0] for x in fused) or 1.0
    fused = [(v / mx, sem, kwn, w, row, why) for v, sem, kwn, w, row, why in fused]
    if bool(cfg.get("rrf_dynamic", True)) and len(fused) >= 4:
        vals = sorted(x[0] for x in fused)
        p75 = vals[int(0.75 * (len(vals) - 1))]
        keep = [x for x in fused if x[0] >= p75]
        minr = max(1, int(cfg.get("rrf_min_results") or 4))
        if len(keep) < minr:
            have = {id(x) for x in keep}
            for x in fused:
                if len(keep) >= minr:
                    break
                if id(x) not in have:
                    keep.append(x)
                    have.add(id(x))
        fused = keep
    return fused


def _fact_scope(cid):
    """会话 key（群号正数 / 私聊负数）→ facts.src 的存储键（str(gid)）。

    R1（2026-11-04）：Prompt 注入必须按会话过滤。facts.src 一直是 str(gid)，
    turns.src 才是 "group:/private:" —— 两套键别混用。
    """
    try:
        return str(int(cid))
    except (TypeError, ValueError):
        return ""


def recall(gid, query="", limit=10, bump=True, scope=None):
    """检索画像事实。**2026-10-26 起是混合召回**：

        0.55 × 语义（向量余弦；没向量就字符二元组余弦）
      + 0.30 × 关键词（LIKE 命中数，本群说的加权）
      + 0.15 × 份量（初始重要度 × 时间衰减 × 复述加成）

    为什么不全押向量：中文短句的向量余弦底噪很高（无关句也有 0.3~0.45），
    单靠它会把「不相干的旧事」排到前面；关键词恰好能把「考研」这种硬信号钉住。
    为什么不全押关键词：换个说法就抓不到（「研究生考试」≠「考研」）——
    这就是向量要补的那 55%。

    传空 query 就返回最近记录的事实（老行为不变）。gid 只用于加权；
    **Prompt 注入必须传 scope=当前会话 key**，此时 SQL 按 facts.src 过滤（R1）。
    """
    if not _DB:
        return []
    cfg = _mcfg()
    gid = str(gid)
    q = (query or "").strip()
    _scope = _fact_scope(scope) if scope is not None else ""
    try:
        with _conn() as c:
            if not q:
                # R1：空 query（“列最近记的”）也必须按会话过滤，否则 scope 形同虚设。
                _e_sql = ("SELECT id,qq,name,content,kind,ts FROM facts")
                _e_args = []
                if _scope:
                    _e_sql += " WHERE src=?"
                    _e_args.append(_scope)
                _e_sql += " ORDER BY ts DESC LIMIT ?"
                _e_args.append(limit)
                rows = c.execute(_e_sql, tuple(_e_args)).fetchall()
                return [{"id": r[0], "qq": r[1], "name": r[2], "content": r[3],
                         "kind": r[4], "ts": r[5], "score": 0.0} for r in rows]

            segs = [s for s in re.split(r"[^\w\u4e00-\u9fa5]+", q) if len(s) >= 2]
            if not segs:
                segs = [q[:4]]

            # —— ① 关键词：SQL LIKE 打全表（不受候选池限制）——
            kw, by_id = {}, {}
            _kw_sql = ("SELECT id,qq,name,content,kind,ts,src,weight,hits,vec "
                       "FROM facts WHERE content LIKE ?")
            if _scope:
                _kw_sql += " AND src=?"
            for s in segs:
                _kw_args = ["%" + s + "%"] + ([_scope] if _scope else [])
                for r in c.execute(_kw_sql, tuple(_kw_args)).fetchall():
                    by_id[r[0]] = r
                    kw[r[0]] = kw.get(r[0], 0) + (2 if str(r[6]) == gid else 1)

            # —— ② 人名 / QQ：反查后把这个人的画像整体抬一手 ——
            hit_qq = set()
            for s in segs:
                for r in c.execute("SELECT qq FROM people WHERE name LIKE ? OR qq LIKE ?",
                                   ("%" + s + "%", "%" + s + "%")).fetchall():
                    hit_qq.add(r[0])
                for r in c.execute("SELECT qq FROM person WHERE gid=? AND "
                                   "(name LIKE ? OR qq LIKE ?)",
                                   (gid, "%" + s + "%", "%" + s + "%")).fetchall():
                    hit_qq.add(r[0])
            if hit_qq:
                marks = ",".join("?" * len(hit_qq))
                _pq_sql = ("SELECT id,qq,name,content,kind,ts,src,weight,hits,vec "
                           "FROM facts WHERE qq IN (%s)" % marks)
                _pq_args = list(hit_qq)
                if _scope:
                    _pq_sql += " AND src=?"
                    _pq_args.append(_scope)
                for r in c.execute(_pq_sql, tuple(_pq_args)).fetchall():
                    by_id[r[0]] = r
                    kw[r[0]] = kw.get(r[0], 0) + 3

            # —— ③ 语义：候选池取最近 POOL 条（够一个群用好几年）——
            sem, method = {}, {}
            qvec = embed(q) if cfg.get("hybrid", True) else None
            if cfg.get("hybrid", True):
                _pl_sql = ("SELECT id,qq,name,content,kind,ts,src,weight,hits,vec "
                           "FROM facts")
                _pl_args = []
                if _scope:
                    _pl_sql += " WHERE src=?"
                    _pl_args.append(_scope)
                _pl_sql += " ORDER BY ts DESC LIMIT ?"
                _pl_args.append(POOL)
                pool = c.execute(_pl_sql, tuple(_pl_args)).fetchall()
                qbg = _bigrams(q)
                for r in pool:
                    by_id.setdefault(r[0], r)
                    v = _unblob(r[9]) if r[9] else None
                    if qvec and v:
                        s, line, how = _cos_dense(qvec, v), SEM_MIN_DENSE, "vec"
                    else:
                        _bg = _bigrams(r[3])
                        # 余弦会被长事实稀释，覆盖率又太宽松 —— 取两者的大者
                        s = max(_cos_map(qbg, _bg), _cover_map(qbg, _bg))
                        line, how = SEM_MIN_BIGRAM, "bigram"
                    if s >= line:
                        sem[r[0]] = s
                        method[r[0]] = how
        if not by_id:
            return []
    except Exception:
        log.warning("记忆检索失败", exc_info=True)
        return []

    # —— ④ 合并打分（在事务外算，纯 CPU）——
    scored = []
    for fid, r in by_id.items():
        kwn = min(1.0, kw.get(fid, 0) / 3.0)
        s = sem.get(fid, 0.0)
        w = _fact_weight(r[7], r[5], r[8], cfg)
        if cfg.get("hybrid", True):
            total = W_SEM * s + W_KW * kwn + W_WT * w
        else:
            total = kwn
        # 关键词和语义**都没命中**就别硬凑（份量只是排序项，不是入围资格）——
        # 否则「查一个不存在的东西」会把最近的事实全捞出来，看着像记忆错乱。
        if kwn <= 0 and s <= 0:
            continue
        if kwn and s:
            why = "关键词+语义"
        elif kwn:
            why = "关键词"
        else:
            why = "语义"
        scored.append((total, s, kwn, w, r, why))
    # —— ④b 阶段 D：RRF 融合（默认 linear，不改变旧行为）——
    # 三信号分别排名后按 1/(k+rank) 融合：对「语义分 0.42 / 关键词计数 6 / 份量 0.8」
    # 这种尺度完全不同的分数不敏感；动态阈值按本次候选的 P75 取，至少保留 min_results 条。
    if str(cfg.get("fusion") or "linear").lower() == "rrf":
        scored = _rrf_rerank(scored, cfg)
    scored.sort(key=lambda x: (-x[0], -x[4][5]))
    top = scored[:max(1, int(limit))]
    out = [{"id": r[0], "qq": r[1], "name": r[2], "content": r[3], "kind": r[4],
            "ts": r[5], "score": round(total, 3), "sem": round(s, 3),
            "kw": round(kwn, 3), "weight": round(w, 3), "why": why,
            "via": method.get(r[0], "")}
           for total, s, kwn, w, r, why in top]

    # —— ⑤ 记一次「用过」：下次同一条会更容易被想起来（复述效应）——
    if bump and out:
        ids = [x["id"] for x in out if x["score"] >= 0.2]
        if ids:
            try:
                with _LOCK, _conn() as c:
                    c.execute("UPDATE facts SET hits=COALESCE(hits,0)+1, last_hit=? "
                              "WHERE id IN (%s)" % ",".join("?" * len(ids)),
                              tuple([time.time()] + ids))
            except Exception:
                log.debug("记 hits 失败（不影响检索）", exc_info=True)
    return out


def recall_block(query, gid=None, n=None, chars=None, scope=None):
    """**分层注入的那一层**：只挑跟「这一句话」有关的 3~5 条旧记忆。

    为什么需要它：`profile_block` 是常驻的（谁是谁），但把几百条事实全塞进
    system prompt 是不可能的 —— 既撑爆上下文，也会让模型把不相干的老事硬拗进来。
    所以这里按当前这句话召回，**带字数硬上限**，一条一条塞到装不下为止。

    返回空串 = 这轮没有值得提的旧事（调用方直接跳过，别拼空段）。
    """
    cfg = _mcfg()
    q = str(query or "").strip()
    if not _DB or len(q) < 2:
        return ""
    try:
        n = int(n or cfg.get("recall_n") or 4)
        budget = int(chars or cfg.get("recall_chars") or 320)
        floor = float(cfg.get("recall_min") or 0.2)
    except Exception:
        n, budget, floor = 4, 320, 0.2
    rows = recall(gid, q, limit=max(n * 2, 6), scope=scope)
    picks, used = [], 0
    for r in rows:
        if float(r.get("score") or 0) < floor:
            continue
        nm = (r.get("name") or "").strip()
        line = "- %s%s" % (("%s：" % nm) if nm else "", (r.get("content") or "").strip())
        if len(line) <= 4:
            continue
        if picks and used + len(line) > budget:
            break
        picks.append(line[:budget])
        used += len(line)
        if len(picks) >= n:
            break
    if not picks:
        return ""
    return ("【跟你刚说的这句有关的旧记忆】\n" + "\n".join(picks) +
            "\n（只是提醒，能接就自然带一句，别生硬复述，也别硬拗成话题）\n")


def forget(gid=None, fact_id=None, qq=None):
    """遗忘：按 id 删一条，或按 qq 删某人的全部画像。记忆会记错，必须能撤退。"""
    if not _DB:
        return 0
    try:
        with _LOCK, _conn() as c:
            if fact_id:
                n = c.execute("DELETE FROM facts WHERE id=?", (int(fact_id),)).rowcount
                c.execute("DELETE FROM fact WHERE id=?", (int(fact_id),))
            elif qq:
                n = c.execute("DELETE FROM facts WHERE qq=?", (str(qq),)).rowcount
                c.execute("DELETE FROM fact WHERE qq=?", (str(qq),))
                c.execute("DELETE FROM people WHERE qq=?", (str(qq),))
                c.execute("DELETE FROM person WHERE qq=?", (str(qq),))
            elif gid:
                n = c.execute("DELETE FROM facts WHERE src=?", (str(gid),)).rowcount
                c.execute("DELETE FROM fact WHERE gid=?", (str(gid),))
                c.execute("DELETE FROM person WHERE gid=?", (str(gid),))
            else:
                n = 0
            return n
    except Exception:
        log.warning("遗忘失败", exc_info=True)
        return 0


# ---------------------------------------------------------------- 跨场合对话流
def log_turn(src, who, qq, text, reply="", persona=""):
    """记一轮真实对话。src = 'group:123450001' / 'private:123450003'。

    `persona` = **说这句话时生效的人格**（2026-10-26 加）。Reflection 靠它做隔离；
    传空表示"不知道是谁说的"，下游会跳过这条，不会默认归给当前人格。
    """
    if not _DB:
        return
    text = str(text or "").replace("\n", " ").strip()[:300]
    reply = str(reply or "").replace("\n", " ").strip()[:300]
    if not text and not reply:
        return
    try:
        with _LOCK, _conn() as c:
            c.execute("INSERT INTO turns(ts,src,who,qq,text,reply,persona) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (time.time(), str(src), (who or "")[:30], str(qq or ""), text, reply,
                       (str(persona or "")[:40] or None)))
            n = c.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            if n > MAX_TURNS:
                c.execute("DELETE FROM turns WHERE id IN (SELECT id FROM turns "
                          "ORDER BY id ASC LIMIT ?)", (n - MAX_TURNS,))
    except Exception:
        log.debug("写对话流失败（不影响主流程）", exc_info=True)


def turns_since(cursor=0, limit=500):
    """按 id 游标取 turns（给记忆卡摄取用，2026-11-03）。

    返回行：(id, ts, src, who, qq, text, reply)。游标由调用方保存（记忆卡 meta 里），
    这里只管按 id 递增读，不做任何过滤 —— 过滤/打分/遗忘是记忆卡自己的事。
    """
    if not _DB:
        return []
    try:
        with _conn() as c:
            return c.execute("SELECT id,ts,src,who,qq,text,reply FROM turns "
                             "WHERE id > ? ORDER BY id ASC LIMIT ?",
                             (int(cursor), int(limit))).fetchall()
    except Exception:
        return []


def recent_turns(n=20, exclude_src=None, since_sec=None, only_src=None):
    if not _DB:
        return []
    try:
        _sql = "SELECT ts,src,who,qq,text,reply FROM turns"
        _args = []
        if only_src:
            _sql += " WHERE src=?"
            _args.append(str(only_src))
        _sql += " ORDER BY id DESC LIMIT ?"
        _args.append(int(n) * 4)
        with _conn() as c:
            rows = c.execute(_sql, tuple(_args)).fetchall()
    except Exception:
        return []
    out = []
    now = time.time()
    for r in rows:
        if since_sec and now - r[0] > since_sec:
            continue
        if exclude_src and str(r[1]) == str(exclude_src):
            continue
        out.append({"ts": r[0], "src": r[1], "who": r[2], "qq": r[3],
                    "text": r[4], "reply": r[5]})
        if len(out) >= n:
            break
    return list(reversed(out))


def private_contacts(limit=40):
    """**聊过私聊的人**（会话总控要按人一行，2026-10-26 主人：「私聊要分开」）。

    `turns.src` 形如 `private:<QQ>` / `group:<群号>`，这里只挑私聊那批，
    按最后说话时间倒序。名字取 `people` 账本里的（跨群通用的那个昵称）。
    """
    if not _DB:
        return []
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT src, MAX(ts) AS last, COUNT(*) AS n FROM turns "
                "WHERE src LIKE 'private:%' GROUP BY src ORDER BY last DESC LIMIT ?",
                (int(limit),)).fetchall()
            out = []
            for src, last, n in rows:
                uid = str(src).split(":", 1)[1] if ":" in str(src) else ""
                if not uid:
                    continue
                nm = c.execute("SELECT name FROM people WHERE qq=?", (uid,)).fetchone()
                out.append({"uid": uid, "name": (nm[0] if nm and nm[0] else ""),
                            "last_ts": last or 0, "turns": int(n or 0)})
        return out
    except Exception as e:
        log.debug("列私聊成员失败: %s", e)
        return []


def person_name(qq):
    """QQ → 昵称（账本里没有就回空串）。私聊成员表用。"""
    if not _DB or not str(qq or "").strip():
        return ""
    try:
        with _conn() as c:
            r = c.execute("SELECT name FROM people WHERE qq=?", (str(qq),)).fetchone()
        return (r[0] if r and r[0] else "")
    except Exception:
        return ""


# ---------------------------------------------------------------- 注入用
def _person_facts(qq, limit=12, scope=None):
    if not _DB or not qq:
        return []
    _scope = _fact_scope(scope) if scope is not None else ""
    try:
        with _conn() as c:
            _sql = "SELECT content FROM facts WHERE qq=?"
            _args = [str(qq)]
            if _scope:
                _sql += " AND src=?"
                _args.append(_scope)
            _sql += " ORDER BY ts DESC LIMIT ?"
            _args.append(limit)
            rows = c.execute(_sql, tuple(_args)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def render_person(qq, name="", scope=None):
    """把某个人的账渲染成可注入的一段。

    Prompt 注入必须给 scope=当前会话 key（R1：只含本会话说过的）；不给才回退旧的全局账。
    """
    if not _DB or not str(qq or "").strip():
        return ""
    qq = str(qq)
    try:
        with _conn() as c:
            p = c.execute("SELECT name,interactions,last_seen FROM people WHERE qq=?",
                          (qq,)).fetchone()
    except Exception:
        p = None
    facts = _person_facts(qq, scope=scope)
    if not p and not facts:
        return ""
    nm = (p[0] if p and p[0] else (name or qq))
    lines = []
    if facts:
        lines.append("你了解他：" + "；".join(facts[-12:]))
    if p and p[1]:
        lines.append("你们互动过 %d 次" % int(p[1]))
    if not lines:
        return ""
    return "【关于 %s（%s）的一本账】\n%s\n" % (nm, qq, "\n".join(lines))


def render_recent(n=20, exclude_src=None, only_src=None):
    """把最近的真实对话渲染成一段（Prompt 注入时必须给 only_src=当前会话）。

    🔴 2026-10-30 主人第四次骂：「我说让 bot 认清每个人的 QQ」「每个人说的话你都给我
       单独分了呀，你不要这个人说的话安给另一个人」。
       这里以前只写 `who`（群名片）——同一群里两个名片相似、或者干脆空名片的人
       （满穗的 card 实测就是空，只能拿 nickname 顶），模型分不清谁说的，必然串人。
       **QQ 是唯一稳定标识，必须带上。**（号码只进 system，出站时 `strip_qq_numbers`
       会硬抹，不会漏到群里。）
    """
    rows = recent_turns(n, exclude_src=exclude_src, only_src=only_src)
    if not rows:
        return ""
    lines = []
    for r in rows:
        where = "群里" if str(r["src"]).startswith("group") else "私聊"
        when = time.strftime("%H:%M", time.localtime(r["ts"]))
        nm = r["who"] or "某人"
        qq = str(r.get("qq") or "").strip()
        who_tag = ("%s（QQ %s）" % (nm, qq)) if qq else nm
        if r["text"]:
            lines.append("  %s %s（%s）：%s" % (when, who_tag, where, r["text"]))
        if r["reply"]:
            lines.append("  %s 你：%s" % (when, r["reply"]))
    if not lines:
        return ""
    return ("【最近的真对话（各个群和私聊合在一起，按时间排；"
            "括号里的 QQ 是你认人用的，别念出来）】\n" + "\n".join(lines) +
            "\n（这些是真说过的，聊到就自然接上，别装作不知道，也别生硬复述；"
            "**别把甲说的话算到乙头上**）\n")


def profile_block(gid=None, limit_people=8, limit_facts=14, scope=None):
    """给 system prompt 用的「你认识这些人」段落（按人数控长，别撑爆上下文）。"""
    if not _DB:
        return ""
    ps = people(gid, limit_people) if gid else people(None, limit_people)
    if not ps:
        return ""
    lines = []
    for p in ps:
        bits = _person_facts(p["qq"], 4, scope=scope)
        nm = p["name"] or ("群友%s" % str(p["qq"])[-4:])
        if bits:
            lines.append("- %s(%s)：%s" % (nm, p["qq"], "；".join(bits)))
        else:
            lines.append("- %s(%s)：聊过 %s 次，还没记下什么特别的事"
                         % (nm, p["qq"], p["count"]))
    head = ("【你长期记得的人】（跨会话记住的，可能不在最近聊天记录里；"
            "聊到相关时自然带出来，别突然硬复述，也别编没记过的细节）")
    return head + "\n" + "\n".join(lines[:limit_people])


def stats():
    if not _DB:
        return {"ok": False, "error": "未初始化"}
    try:
        with _conn() as c:
            n_person = c.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            n_fact = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            n_turn = c.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            gs = sorted({r[0] for r in c.execute("SELECT DISTINCT gid FROM person").fetchall()})
        return {"ok": True, "db": str(_DB), "people": n_person, "facts": n_fact,
                "turns": n_turn, "groups": gs, "embed": embed_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
