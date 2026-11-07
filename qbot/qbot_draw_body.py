_draw_lock = threading.Lock()
_draw_q = queue.Queue()
_draw_state = {"busy": False, "last": 0.0, "count": 0, "since": 0.0,
               "seq": 0, "up": None, "up_t": 0.0}
# 运行时覆盖位，现在恒为 None：开关的真相在 config（comfyui.enabled），
# 「画图 开/关」和「设置 现画」都写 config 并把它清回 None。留着是为了兼容旧引用。
_draw_switch = None
# 引擎白名单（铁律 1）：只认这三档，别的档名一律落回默认档，杜绝摸回 SD1.5
_ENGINE_CHOICES = ["anima", "krea2", "fast"]


_draw_results_lock = threading.Lock()
_draw_results = {}          # jid → 这次画图任务的真实终态（queued / done / failed）
_DRAW_RESULT_KEEP = 20


def _draw_result_set(jid, **fields):
    """记一次画图任务的真实终态；只留结构化事实，不产生用户可见文字。

    终态只许前进不许倒退：done/failed 落地后，晚到的 queued/running 不许覆盖
    （2026-11-07 审阅：worker 与 do_draw 并发时 running 能盖掉 failed）。
    """
    if jid is None:
        return
    with _draw_results_lock:
        rec = _draw_results.setdefault(int(jid), {"id": int(jid)})
        _FWD = {"queued": 0, "running": 1, "done": 2, "failed": 2}
        new = _FWD.get(fields.get("status"))
        if new is not None:
            old = _FWD.get(rec.get("status"), -1)
            if new < old:
                return                      # 终态已落，晚到的旧状态丢弃
            if new == old and rec.get("done"):
                return
        rec.update(fields)
        rec["ts"] = time.time()
    if len(_draw_results) > _DRAW_RESULT_KEEP:
        with _draw_results_lock:
            for old in sorted(_draw_results, key=lambda k: _draw_results[k].get("ts") or 0)[:-_DRAW_RESULT_KEEP]:
                _draw_results.pop(old, None)


def _draw_result_last(gid=None):
    """最近一次（可按会话过滤）画图任务终态；给状态查询如实报，不猜。"""
    with _draw_results_lock:
        rows = [dict(v) for v in _draw_results.values()
                if gid is None or v.get("gid") == int(gid)]
    if not rows:
        return None
    row = max(rows, key=lambda r: r.get("ts") or 0)
    row.pop("ts", None)
    return row


def _comfy_cfg():
    return CFG.get("comfyui") or {}


def _moss_cfg():
    """MOSI 云出图配置（config.json 的 `moss` 段，见 moss_gen.py）。"""
    return CFG.get("moss") or {}


def _moss_studio_status():
    """「排版链状态」只读旋钮的值：Studio 就绪路径 + 降级说明（主人看得懂的话）。"""
    try:
        from qbot import moss_layout
        p = moss_layout.studio_dir(_moss_cfg())
        note = str(moss_layout.last_note() or "")
        if p:
            return "排版链就绪：%s%s" % (p, ("；" + note) if note else "")
        return "Studio 未找到——排版/挑词已降级为「原始描述直传」，去 MossTool 检查工程目录"
    except Exception as e:  # noqa: BLE001
        return "状态读不出：%s" % _short_err(e)


def _draw_groups(engine):
    """某条生图链路**允许使用**的群号集合（config 的 `draw.<engine>.groups`）。

    语义（主人 2026-10-24 定）：**选了就开、没选就关** —— 这是白名单，空集 = 谁都不能用。
    """
    node = ((CFG.get("draw") or {}).get(engine) or {})
    out = set()
    for g in (node.get("groups") or []):
        try:
            gi = int(g)
        except (TypeError, ValueError):
            continue
        # 负数 = 私聊会话 key（`priv_key(uid)`），不是群。私聊走 `draw.<engine>.private`，
        # 混进群白名单只会让「会话总控」多出一行「群名暂缺的群」（2026-10-26 修）
        if gi > 0:
            out.add(gi)
    return out


def draw_set_group_engine(gid, engine, on, src="qq"):
    """把某个群从某条链路里加/移。返回变更后的群列表。"""
    if engine not in ("comfyui", "moss"):
        raise ValueError("engine 只能是 comfyui / moss")
    d = CFG.setdefault("draw", {})
    node = d.setdefault(engine, {})
    lst = []
    for g in (node.get("groups") or []):
        try:
            gi = int(g)
        except (TypeError, ValueError):
            continue
        if gi not in lst:
            lst.append(gi)
    gid = int(gid)
    if on:
        if gid not in lst:
            lst.append(gid)
    else:
        lst = [g for g in lst if g != gid]
    node["groups"] = lst
    save_config()
    log.info("生图链路[%s]: 群 %s %s %s（%s 现 %s）", src, gid,
             "开启" if on else "关闭", engine, engine, lst)
    return lst


def _private_draw_default(engine):
    """私聊**所有人**的默认链路开关：`draw.<engine>.private` → 老 `draw.private` → 开。"""
    d = CFG.get("draw") or {}
    if engine in ("comfyui", "moss"):
        node = d.get(engine) or {}
        if "private" in node:
            return bool(node.get("private"))
    return bool(d.get("private", True))


def _private_draw_ok(gid, engine=None):
    """私聊（**负数会话 key**）默认允许画图。

    🔴 2026-10-25 主人实测踩到：私聊发「moss画 …」直接被判「这个群没开画图」。
       原因是 10-24 改的「按群白名单」是给**群**用的，而私聊的 gid 是
       `priv_key(uid)`（负数），永远不可能出现在 `draw.moss.groups` 里。
       私聊本来就只有主人/白名单能进来（`private.admin_only`），所以默认放行；
       想关掉配 `draw.private=false`。

    2026-10-26 会话总控加了**按链路**的私聊开关：`draw.comfyui.private` /
    `draw.moss.private`，没配就沿用老的 `draw.private`（两边一起关）。

    2026-10-26 晚（主人：「私聊要分开，每个成员都不同」）：最优先看**这个人自己**的配置
    `session.private.<QQ>.comfyui|moss`；没配才落到上面那些「所有人默认」。
    """
    try:
        g = int(gid or 0)
    except (TypeError, ValueError):
        return False
    if g >= 0:
        return False
    if engine in ("comfyui", "moss"):
        v = qbot_session.raw(g, engine)      # 按人配过就用它（没配是 None）
        if isinstance(v, bool):
            return v
    d = CFG.get("draw") or {}
    base = bool(d.get("private", True))
    if engine in ("comfyui", "moss"):
        node = d.get(engine) or {}
        if "private" in node:
            return bool(node.get("private"))
    return base


def engine_on(gid, engine):
    """这个会话能不能用这条链路（群看白名单，私聊默认放行）。"""
    try:
        gi = int(gid)
    except (TypeError, ValueError):
        return False
    if gi in _draw_groups(engine):
        return True
    return _private_draw_ok(gi, engine)


def draw_engine(gid=None):
    """**这个会话**该走哪条生图链路；None = 两条都没开（画不了）。

    规则（主人 2026-10-24 定：两条链路各自按群开关，选了开、没选关）：
      · 两条都开 → 用 `comfyui.engine` 里选的默认（网页/「画图 引擎」可改）
      · 只开一条 → 就用那条
      · 都没开   → None
    私聊（负数 key）不看白名单，默认按偏好走（见 `_private_draw_ok`）。
    """
    try:
        gid = int(gid or 0)
    except (TypeError, ValueError):
        gid = 0
    on_c = gid in _draw_groups("comfyui") or _private_draw_ok(gid, "comfyui")
    on_m = gid in _draw_groups("moss") or _private_draw_ok(gid, "moss")
    if on_c and on_m:
        e = str(_comfy_cfg().get("engine") or "comfyui").strip().lower()
        return "moss" if e in ("moss", "mosi", "cloud", "云", "云端") else "comfyui"
    if on_m:
        return "moss"
    if on_c:
        return "comfyui"
    return None


def engine_on_here(gid, engine):
    """这个会话**这条链路**开没开：群看白名单，私聊看 `draw.<engine>.private`。"""
    eng = "moss" if str(engine or "").lower() == "moss" else "comfyui"
    try:
        g = int(gid or 0)
    except (TypeError, ValueError):
        g = 0
    return g in _draw_groups(eng) or _private_draw_ok(g, eng)


# 本地 ComfyUI 的档位引擎名（`comfy_gen.style_dict()` 的 `engine` 值）。
# 🔴 云端 Moss 只吃自然语言布局提示词，**没有** krea2 / anima / 厚涂 / 宫格 这些 LoRA 档
#    —— 名字能对上，出来的图完全不是那回事。所以「指定了本地档」就必须走本地。
LOCAL_STYLE_ENGINES = ("krea2", "anima", "wf", "fast")


def style_local_engine(style):
    """这个档是不是本地 ComfyUI 档：是就返回引擎名，不是（或认不出）返回 None。"""
    n = str(style or "").strip()
    if not n or comfy_gen is None:
        return None
    try:
        s = comfy_gen.style_dict(_comfy_cfg(), n)
    except Exception:
        return None
    if not s:
        return None
    e = str(s.get("engine") or "").strip().lower()
    return e if e in LOCAL_STYLE_ENGINES else None


def moss_enabled(gid=None):
    """moss 这条路能不能用：这个群开了 + 有 key + 模块在 + 全局总闸没关。

    ⚠️ 与 comfy 的区别：**不看「画图 开」那个全局开关**（主人 2026-10-24 明确要求
       「一种是管理员说画图开启才能用的 comfyUI 流，还有一种就是这个」），
       moss 是云端算力，不占显存、不需要预热。但**按群白名单**照样管。
    """
    if moss_gen is None or not moss_gen.available(_moss_cfg()):
        return False
    if gid is not None and not engine_on(gid, "moss"):
        return False
    return bool(_comfy_cfg().get("enabled", True))


# ---- 画图路由 ----
#   固定口令认指令，「画…」后面的词直接给工作流；口令没认出的消息交主脑
#   （它有 draw_image/draw_like_this 工具面，自己判断是不是在求画）。
#   旧「ai 二次判定」（单独发一次 chat() 问「是不是画图」）已删：那是重复决策层（ADR-001 收口，2026-11-06）。


def draw_route():
    """画图路由模式（兼容旧旋钮读取）：现在只有固定口令一条，口令没认出的一律落主脑。"""
    v = str(_dot_get("draw.route") or "").strip().lower()
    return "ai" if ("ai" in v or "判断" in v or "智能" in v or "模型" in v) else "keyword"


# 画图描述外面那层「包裹标点」：判定「画 [吧]」这类白板指令时先剥掉再说话
_DRAW_WRAP_PUNCT = "[]【】〖〗{}()（）<>《》「」『』 \t ，,。．、~～!！?？;；:：'\"“”‘’·-*—_="


def _draw_desc_ok(what):
    """画图描述是不是「有内容」：剥掉包裹标点后**全是**语气词/空 = 白板指令。

    「画 [吧]」「moss画个」都是白板（剥完啥也不剩）；「画一只猫吧」不是（还剩「猫」）。
    2026-10-29 主人一天晒了两张图：「画 [吧]」拿「[吧]」画、「moss画个」拿「个」画。
    """
    bare = str(what or "").strip(_DRAW_WRAP_PUNCT)
    if not bare:
        return False
    if len(bare) <= 2 and not re.sub(r"[个张只幅条片吧吗呢嘛哦啊呀哇哈呐咯？?！!～~。]", "", bare):
        return False
    return True


def _moss_layout_on():
    """Moss 生图要不要先走「布局提示词」那一步（Studio 的提示词工程）。

    `moss.prompt_mode`：layout（默认，一句话→整画面 Layout→编译成 Moss JSON 提示词）
                      / raw（直接把描述当提示词，省十几秒）。
    """
    if moss_layout is None:
        return False
    return str(_moss_cfg().get("prompt_mode") or "layout").strip().lower() != "raw"


def _studio_settings():
    """读 Studio 的 `data/settings.json`（base_url / api_key / model / vision_model）。

    2026-10-25 主人：「我那个 moss-prompt-studio 都配好了」—— 那就直接吃它的配置，
    bot 侧不再自己猜模型。Studio 里换模型，这边跟着变。
    """
    if moss_layout is None:
        return {}
    try:
        p = Path(moss_layout.studio_dir(_moss_cfg())) / "data" / "settings.json"
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _studio_models():
    """Studio 语言档的尝试顺序：主档 → `fallback_models`（Studio 自己配的备用链），
    其中**刚挂过的档排到最后**（见 `_studio_model_dead()`）。

    🔴 2026-10-31 实测：主档 `loomy/DeepSeek-V4.1-Flash` 会**间歇 502**（同一条请求
       刚才还好好回，下一分钟就 Bad Gateway）。而 Studio 的 settings.json 里明明配了
       `fallback_models: raccoon/sn-glm-5-3-flash, loomy/GLM-5.3-Flash, ...`，
       `_studio_chat` 却**只认主档**，一挂就整个回落 modelscope（60 秒一趟）。
       主人在群里等二十来秒，等到的还是「布局失败」。
       ⇒ 这里按 Studio 那份清单逐个试，谁先回用谁。

    🔴 2026-10-31 补（主人「你说这么多，没考虑我之前说的问题吗？都要改啊」后实测）：
       主档 502 后**每次调用还要先撞它一次**（挑词 + 查证 + 布局各撞一遍 = 白等 90 秒）。
       加熔断：刚 502/超时过的档 3 分钟内排到链尾先试别人；它回暖后自动回到前面。

    🔴 2026-10-31 第三次修（**这是拖慢整条链的真凶**）：
       Studio 那几个备用档全是**思考型**，实测拿 2000/3000 token 全用在思考上：
         · `raccoon/sn-glm-5-3-flash`：出词 3000、**其中思考 2992**、finish=length、正文空 ⇒ 白等 ~40 秒
         · `loomy/GLM-5.3-Flash` / 主档：间歇 502
       一次查证要把这仨挨个撞一遍 ⇒ 132 秒才拿到资料（主人在群里等两分多钟）。
       实测 89 个模型里挑出的快档：`buddy/kimi-k3.1-pro` **4.8~15.8 秒**、
       出词量只有思考模型的三分之一（394 vs 1255）、答案一样准
       （妄想天使三人组 → 南宫羽粉发光环 SURIEL / 千夏绿发喇叭锤 / 爱芮机器人形态全对）。
       ⇒ 在思考型备用档**之前**插入这张快档表（`_STUDIO_FAST`）。
    """
    s = _studio_settings()
    first = str(s.get("model") or "").strip()
    out = [first] if first else []
    for m in _STUDIO_FAST:                 # 快档优先于思考型备用档
        if m not in out:
            out.append(m)
    for x in str(s.get("fallback_models") or "").split(","):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    now = time.time()
    live = [m for m in out if _STUDIO_COOL.get(m, 0) <= now]
    dead = [m for m in out if _STUDIO_COOL.get(m, 0) > now]
    return live + dead


# 18900 聚合站上实测「非思考型 / 思考占比低 / 快」的文本档。
# 主人在后台能改的是 Studio 自己那份 settings.json；这里只当**链内快档**兜底。
_STUDIO_FAST = ["buddy/kimi-k3.1-pro", "minimax/MiniMax-M3.1-Flash"]


def _studio_models_fast():
    """只要快档（挑词/查证这类**短问答**用）：思考型模型在短任务上反而更慢更废。

    实测同一句「从画图描述里挑角色名」：快档 4.8 秒出 84 词，思考型 47 秒出 715 词
    其中 709 是思考 —— 结果一样，代价差十倍。所以短任务直接走快档，不走主档链。
    """
    now = time.time()
    return [m for m in _STUDIO_FAST if _STUDIO_COOL.get(m, 0) <= now]


_STUDIO_COOL = {}          # model -> 冷却到期 epoch
_STUDIO_COOL_SEC = 180     # 挂过的档冷静 3 分钟


def _studio_model_dead(mdl):
    """把一个 Studio 档判死一小会儿（502/超时/空回复都算）。"""
    if mdl:
        _STUDIO_COOL[str(mdl)] = time.time() + _STUDIO_COOL_SEC



def _studio_chat(messages, model=None, timeout=180, max_tokens=2500):
    """打 Studio 配的那个 OpenAI 兼容端点。返回 (文本, 模型名)。失败抛异常。

    `model` 留空时按 `_studio_models()` 逐个试（主档 502 就自动换备用档）。
    """
    s = _studio_settings()
    url = (s.get("base_url") or "").strip().rstrip("/")
    if not url:
        raise RuntimeError("Studio 没配 base_url")
    order = [str(model).strip()] if model else _studio_models()
    order = [m for m in order if m]
    if not order:
        raise RuntimeError("Studio 没配 model")
    errs = []
    for mdl in order:
        try:
            return _studio_chat_one(url, s, mdl, messages, timeout, max_tokens)
        except Exception as e:
            _studio_model_dead(mdl)      # 502/超时/空回复 ⇒ 3 分钟内不再先撞它
            errs.append("%s: %s" % (mdl.split("/")[-1], _short_err(e)))
            log.info("Studio 语言档 %s 不通（%s），试下一个", mdl.split("/")[-1],
                     _short_err(e))
    raise RuntimeError("Studio 语言档全失败（%s）" % "；".join(errs))


def _studio_chat_one(url, s, mdl, messages, timeout, max_tokens):
    body = json.dumps({"model": mdl, "messages": messages,
                       "temperature": 0.3, "max_tokens": int(max_tokens)}).encode()
    req = urllib.request.Request(
        url + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + (s.get("api_key") or "")})
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    ch = (d.get("choices") or [{}])[0]
    txt = ((ch.get("message") or {}).get("content") or "")
    if not txt.strip():
        u = d.get("usage") or {}
        raise RuntimeError("Studio 端点返回空（finish=%s 出词=%s 其中想的=%s）" % (
            ch.get("finish_reason"), u.get("completion_tokens"),
            ((u.get("completion_tokens_details") or {}).get("reasoning_tokens"))))
    return txt, mdl


def _moss_layout_llm():
    """给 moss_layout.from_text 的文本模型回调。

    优先用 Studio 自己配的端点（`<studio>/data/settings.json` 的 base_url/model/key）——
    实测 1.3 秒响应；走 modelscope 要 60 秒。不通再回落 bot 自己的 provider。

    max_tokens 4000：Layout JSON 动辄十几个元素，2500 会把最后几条掐在半截上
    （2026-10-29 那种「Expecting ',' delimiter」有一半功劳在这）。
    """
    mc = _moss_cfg()

    def _call(messages):
        try:
            txt, mdl = _studio_chat(messages, max_tokens=4000)
            log.info("Moss 布局：Studio 端点 %s（规则=%s）", mdl, moss_layout.layout_system_prompt.note)
            return txt
        except Exception as e:
            log.info("Moss 布局：Studio 端点不通（%s），回落 bot provider", _short_err(e))
        name = str(mc.get("layout_provider") or "modelscope").strip()
        return chat_once(name, messages)

    return _call


def _moss_vision_studio(image_path, sys_prompt, user_text):
    """用 **Studio 配的端点**看图（带图打 /chat/completions）。

    模型严格按主人在 Studio 里配的两个来：
      · 视觉任务 → `vision_model`（raccoon/sn-deepseek-v4-1-flash）
      · 语言任务 → `model`（loomy/DeepSeek-V4.1-Flash，见 `_moss_layout_llm`）
    视觉档挂了才兜底用语言档（它其实也吃图，只是不该抢视觉的活）。
    顺序可用 `moss.studio_vision_models` 覆盖。
    """
    import base64 as _b64
    s = _studio_settings()
    if not (s.get("base_url") or "").strip():
        raise RuntimeError("Studio 没配 base_url")
    order = [str(x).strip() for x in (_moss_cfg().get("studio_vision_models") or []) if str(x).strip()]
    if not order:
        # 🔴 2026-10-25 主人纠正：**视觉任务就用他配的视觉模型**。
        # 我之前为了快把 `model`（语言档）排前面，虽然实测更快，但那违背了主人的配置意图
        # （「你看我这个项目用的模型是这两个吧，一个语言，一个视觉」）。
        # 现在严格按 settings.json 的 vision_model → model 顺序，只做兜底。
        order = [s.get("vision_model") or "", s.get("model") or ""]
    order = [m for m in order if m]
    if not order:
        raise RuntimeError("Studio 没配 model / vision_model")
    data_url = reverser.fit_image_data_url(image_path)
    errs = []
    for mdl in order:
        msgs = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}}]},
        ]
        try:
            # 🔴 2026-10-31：2500 提到 3500。反推要吐**整份布局 JSON**（实测 14 个框
            # 正文就上千 token），而 Studio 这两个视觉/语言档都是**思考型**，
            # 思考段还要另吃几百到一两千 ⇒ 2500 一旦被思考吃满，正文返回空串
            # （finish=length、content=''），上层只会看到「视觉模型没给出可用的框」。
            txt, used = _studio_chat(msgs, model=mdl, timeout=200, max_tokens=3500)
            return txt, "studio/%s" % used.split("/")[-1]
        except Exception as e:
            errs.append("%s: %s" % (mdl.split("/")[-1], _short_err(e)))
    raise RuntimeError("Studio 看图全失败（%s）" % "；".join(errs))


def _moss_vision_call(image_path, sys_prompt, user_text):
    """给 moss_layout.from_image 的视觉回调。三级接力：

      ① **Studio 端点**（最快最好，13s / 8 个框）—— 主人已经把 Studio 配好了
      ② 本地 Ollama 直连（原生 /api/chat，约 50s，云端全挂时的保底）
      ③ bot 自己的识图云端链（see_candidates）

    反推要**原样 JSON**，所以云端那条 purify_cjk=False、allow_cjk=True ——
    净化会把中文台词洗成占位符，JSON 也会被搅坏。
    """
    if reverser is None:
        raise RuntimeError("reverser 模块没加载，反推不可用")
    errs = []
    # ① Studio
    try:
        txt, via = _moss_vision_studio(image_path, sys_prompt, user_text)
        log.info("Moss 反推布局 via=%s", via)
        return txt
    except Exception as e:
        errs.append("Studio: %s" % _short_err(e))
        log.info("Moss 反推布局：Studio 没成（%s），试本地", _short_err(e))
    # ② 本地直连
    try:
        txt, via = _moss_vision_local(image_path, sys_prompt, user_text)
        log.info("Moss 反推布局 via=%s（本地直连）", via)
        return txt
    except Exception as e:
        errs.append("本地: %s" % _short_err(e))
        log.info("Moss 反推布局：本地档没成（%s），走云端链", _short_err(e))
    # ③ bot 识图云端链
    pcfg = (CFG.get("ai") or {}).get("providers") or {}
    try:
        text, via = reverser.vl_text(
            str(image_path), CFG,
            provider=reverser.see_provider(CFG),
            model=reverser.see_model(CFG),
            sys_prompt=sys_prompt, user_text=user_text,
            purify_cjk=False, allow_cjk=True,
            chain=reverser.see_candidates(pcfg, CFG), chain_from_see=True)
        log.info("Moss 反推布局 via=%s（云端链）", via)
        return text
    except Exception as e:
        errs.append("云端: %s" % _short_err(e))
    raise RuntimeError("视觉模型全用不了（%s）" % "；".join(errs))


def _moss_vision_local(image_path, sys_prompt, user_text):
    """本地 Ollama 直连（**原生 /api/chat**，不是 /v1）。

    ⚠️ 2026-10-25 踩坑：`reverser._call_vl` 走的是 `/v1` 兼容端点，而 **/v1 会吞掉
    `options.num_gpu`** —— 整个 7B 全量进显存，跟 ComfyUI 抢 8G 直接卡到超时
    （实测走 /v1 180s 超时；直连 /api/chat 带 num_gpu=20 只要 50s）。
    这个坑 2026-10-03 在 `caption_vl()` 里已经记过一次，这里同样处理。
    """
    import base64 as _b64
    mc = _moss_cfg()
    p = (CFG.get("ai") or {}).get("providers", {}).get("ollama") or {}
    url = str(p.get("url") or "").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    if not url:
        raise RuntimeError("没配 ai.providers.ollama.url")
    mdl = str(mc.get("vision_model") or "").strip()
    if not mdl and reverser is not None:
        try:
            mdl = reverser.see_local_model(CFG)
        except Exception:
            mdl = ""
    if not mdl:
        raise RuntimeError("没配本地视觉模型（moss.vision_model / see.local_model）")
    data_url = reverser.fit_image_data_url(image_path)
    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_text,
             "images": [data_url.split(",", 1)[-1]]},
        ],
        "stream": False,
        "keep_alive": int(mc.get("vision_keep_alive", 0) or 0),
        "options": {
            "num_predict": int(mc.get("vision_max_tokens", 1600) or 1600),
            "temperature": 0.3,
            "num_gpu": int(mc.get("vision_num_gpu", 20) or 20),
        },
    }
    req = urllib.request.Request(
        url + "/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    to = int(mc.get("vision_timeout", 240) or 240)
    with _NO_PROXY_OPENER.open(req, timeout=to) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    txt = ((d.get("message") or {}).get("content") or "").strip()
    if not txt:
        raise RuntimeError("本地视觉模型返回空（可能被 num_predict 截断）")
    return txt, "ollama/%s" % mdl.split("/")[-1]


def _moss_probe(messages):
    """给 SubjectBook 挑词/查词用的模型回调：**优先 Studio 端点**。

    ⚠️ 2026-10-25：原来走 `_draw_llm()`（enhance_provider=siliconflow）和
    `_draw_webllm()`（联网小模型），前者 402 余额不足、后者慢，
    于是「主体查证整步失败（HTTP 402），直接照原样画」—— 珂莱塔照样被画成白毛。
    Studio 端点 1~3 秒就有答案，直接用它。

    🔴 2026-10-31 实测改：**max_tokens 从 800 提到 2000**。Studio 那两个语言档都是
       **思考型**（`raccoon/sn-glm-5-3-flash` 实测 800 档烧掉 2612 个思考 token、
       正文返回空串、finish=length；1200 档才正常出词）。800 等于白跑一趟，
       上层拿到空串就判「没查到设定」，然后又退到 60 秒的 modelscope。
    🔴 2026-10-31 实测改（第二次）：`max_tokens` 2000 → **3000**。
       实测链路：`sn-glm-5-3-flash` 在 2000 档时 出词=2000、**其中思考=1999**、
       finish=length、正文空串 ⇒ 白等 20 秒还得换下一个档。
       思考型模型要「先想再答」，2000 根本不够它想完；3000 才留得下正文。

    🔴 2026-10-31 第三次改：**短问答不再走 Studio 主档链，直接走快档**。
       「挑角色名 / 压卡片」都是短任务，思考型模型在这里纯属浪费：
       实测同一句挑词，快档 4.8 秒出 84 词、思考型 47 秒出 715 词（其中 709 是思考）。
       先试 `_studio_models_fast()`（`buddy/kimi-k3.1-pro` 等），全挂了才回主档链。
    """
    for mdl in _studio_models_fast():
        try:
            txt, _m2 = _studio_chat(messages, model=mdl, timeout=60, max_tokens=2000)
            if txt.strip():
                return txt
        except Exception as e:
            _studio_model_dead(mdl)
            log.info("Moss 挑词快档 %s 不通（%s）", mdl.split("/")[-1], _short_err(e))
    try:
        txt, _m = _studio_chat(messages, timeout=90, max_tokens=3000)

        if txt.strip():
            return txt
    except Exception as e:
        log.info("Moss 挑词：Studio 不通（%s），回落 modelscope", _short_err(e))
    return chat_once("modelscope", messages)


def _moss_web_card():
    """Moss 查证用的「联网嘴」：`(messages, query) -> 文本`，给 SubjectBook 当 web 回调。

    🔴 2026-10-31 主人当场抓的现行：
       「@柚大人 画一个妄想天使三人组」→ bot 直接画了三个白毛天使；
       主人追问「你都不搜下妄想天使三人组是谁吗」→ 实测日志里**一次搜索都没有**。
       原因：`_moss_subject_hint()` 调的是 `book.lookup(text, probe=..., web=None)` ——
       `web=None` 等于**只让模型凭记忆答**，答不出就吐「未查到确切出处」，
       然后又因为 `_is_null_answer` 判空 ⇒ 当没查到 ⇒ 布局模型拿六个字瞎编。
       comfy 那条路是靠 `_draw_webllm()` 真联网；Moss 这条完全没接。
       现在接上 **bot 自带的免密钥搜索**（`web_search_any`：付费 key → 搜狗/百度/360/Bing），
       实测「妄想天使 绝区零」4.2 秒拿到 6 条、确认出自《绝区零》。
       **先搜再答**：搜到的是原始网页摘要，塞给 Studio 端点压成 WHO/LOOK/TAGS 卡。
    """
    def _call(messages, query=""):
        q = str(query or "").strip()
        snips = ""
        if q and search_usable():
            try:
                snips = web_search_any(q, count=6) or ""
            except Exception as e:
                log.info("Moss 查证联网搜索失败（%s），只用模型记忆", _short_err(e))
        msgs = list(messages or [])
        if snips:
            msgs.append({"role": "user",
                         "content": "【联网搜到的资料，以它为准（优先立绘、设定、百科类说法）】\n"
                                    + snips})
            log.info("Moss 查证：[%s] 联网搜到 %d 字资料", q[:26], len(snips))
        return _moss_probe(msgs)
    return _call


def _moss_subject_hint(text):
    """画之前先查一下画面里的角色，返回 (资料段, 查到的名字)。

    🔴 2026-10-25 主人实测：`@Yuki 画鸣潮的珂莱塔` → 出来是个**白毛原创角色**
       （珂莱塔是红发）。原因：comfy 那条路会走 SubjectBook 查设定，
       **Moss 这条路完全没接**，布局 LLM 只能拿「珂莱塔」三个字瞎编外观。

    🔴 2026-10-31：`web=None` 改成 `web=_moss_web_card()` —— 不联网就等于没查。
    """
    book = _subject_book()
    if not book:
        return "", []
    try:
        hint, names = book.lookup(text, probe=_moss_probe, web=_moss_web_card())
        if names:
            log.info("Moss 角色查证：%s（资料 %d 字）", "、".join(names), len(hint or ""))
        return hint or "", names or []
    except Exception as e:
        log.info("Moss 角色查证失败（%s），照原样画", _short_err(e))
        return "", []


def _moss_translate_prompt(what, hint=""):
    """布局这步彻底失败时的兜底：把用户的话翻成**一段像样的英文画面描述**。

    🔴 2026-10-31 主人定：**绝不许把中文原话直接发给 Moss** ——
       「为什么还是要把画这个口令直接传给本地？我叫你画什么东西，你如果不知道这是啥，
        肯定要先知道这个东西是什么，你才能继续画呀，然后才是细化扩充提示词。」
       Moss 只吃结构化英文布局，直接喂「妄想天使三人组」六个字 = 让它瞎画，
       出来的图跟用户想要的东西毫无关系（还会因为中文「涩图」这类词撞上游 403）。

    这里不追求布局那份精细度（框位靠 Moss 自己排），只保证**主体、外观、场景、光线、
    风格**都被翻译进去了；查证到的官方设定也一起塞进去。失败返回 ""，由调用方报错。
    """
    sys_p = ("你是 MOSS-Image 的提示词作者。把用户的中文想法写成**一段英文画面描述**，"
             "要写清：主体是谁/长什么样（发色、瞳色、服装、配饰）、在做什么、什么环境、"
             "什么光线、什么色调、什么构图与景别。"
             "**只输出那段英文**：不要 JSON、不要 Markdown、不要中文、不要解释、不要标题。"
             "不要写 masterpiece / best quality 这类空质量词。")
    user = "用户想法：%s" % (what or "")
    if hint:
        user += "\n\n（以下是查到的官方设定，必须严格照它写外观，别自己换颜色或换角色）\n" + hint
    msgs = [{"role": "system", "content": sys_p}, {"role": "user", "content": user}]
    try:
        txt = _moss_layout_llm()(msgs)
    except Exception as e:
        log.warning("Moss 兜底翻译失败：%s", _short_err(e))
        return ""
    txt = (txt or "").strip().strip("`").strip()
    # 它偶尔还是掺中文/给 JSON 壳，这里把关：真没给出英文就不算兜底成功
    if not txt or not re.search(r"[A-Za-z]{3}", txt):
        return ""
    if txt.startswith("{"):
        return ""
    log.info("Moss 兜底翻译：%d 字英文（布局这步没成）", len(txt))
    return " ".join(txt.split())[:1200]


def _moss_prompt(what, aspect, i2i=None, force_layout=False, lookup=True, edit=None,
                 brief=False, reverse_mode=None, detail="medium"):
    """把描述（或参考图）变成 Moss 提示词。返回 (prompt_text, 说明, 查到的角色名)。

    ⚠️ 2026-10-25 主人定：**Moss 只吃布局提示词（结构化 JSON），自然语言它用不了**
       —— 所以「反推」在 moss 引擎下必须产出布局 JSON，不能是英文散文。
       `force_layout=True` 时不管 `moss.prompt_mode` 是不是 raw，都走布局那条。

    `lookup=True` 时先查角色设定（珂莱塔这种），把官方外观塞进布局提示词 ——
    不查的话 LLM 只能瞎编，画出来就是另一个角色。

    `brief=True` 走布局简易档：一幕只有一个主角的描述（沉浸情景模式）不需要十几个框，
    2026-10-29 实测布局这步从 ~25 秒降到 ~10 秒，画面质量没掉。

    失败一律回落原始描述 —— 布局这步是「锦上添花」，不能因为它把整张图搞没。

    🔴 **2026-10-31 改：这条「回落」被主人当场骂穿，分叉处理**
       （原话「为什么还是要把画这个口令直接传给本地？我叫你画什么东西，你如果不知道
         这是啥，肯定要先知道这个东西是什么，你才能继续画呀，然后才是细化扩充提示词」）：

        · **文生图**失败 ⇒ 走 `_moss_translate_prompt()`，把话翻成一段英文画面描述
          （查到的主体设定一并塞进去）。**绝不把中文原话原样发给 Moss**。
        · **图生图**（反推）失败 ⇒ **直接报错、不出图**。旧行为是回落成「就按用户那行字
          画一张」，参考图一个字都没进提示词 —— 主人「给你一张图片，你甚至返还出一张
          风景照」就是这个。乱出一张比明确说画不出来更糟。
    """
    mc = _moss_cfg()
    raw = (what or "").strip()
    _bad = moss_layout.last_note()
    if _bad:
        log.warning("Moss 提示词：%s", _bad)
    if not force_layout and not _moss_layout_on():
        # `prompt_mode=raw`：主人显式选了「直接把描述当提示词」。
        # 🔴 2026-10-31：这个分支同样**不许把中文原话直传** —— 主人骂的就是这个
        #    （「为什么还是要把画这个口令直接传给本地」）。raw 只表示「不为构图切框」，
        #    不表示「不翻译、不查证」。所以这里仍然：查证 → 翻成英文描述。
        log.info("Moss 提示词：prompt_mode=raw，跳过布局但仍做查证+英文转写")
        if moss_layout is None:
            raise RuntimeError("提示词工程模块没加载（moss_layout），画不了")
        if lookup and raw:
            hint, who = _moss_subject_hint(raw)
        alt = _moss_translate_prompt(raw, hint)
        if not alt:
            raise RuntimeError("提示词这步没成（raw 模式的英文转写失败），不敢拿原话硬画")
        return alt, "raw 模式：查证后转成英文描述", who
    if moss_layout is None:
        raise RuntimeError("提示词工程模块没加载（moss_layout），画不了")
    w, h = moss_gen.pick_size(aspect=aspect)
    who, hint = [], ""
    try:
        if i2i:
            # 参考图 → 反推布局（框+描述）→ [按一句话改稿] → 编译
            #
            # 🔴 2026-10-31 真 bug 修复（主人「给你一张图片，你甚至返还出一张风景照，
            #    我也不知道是为啥，明明我平时用我的 moss image studio 的时候都是挺准的」）：
            #    旧代码 `from_image(i2i, ...)` **只把图片丢进去，`what`（用户写的画图要求）
            #    一个字都没进提示词** ⇒ 视觉模型只能照图复刻。
            #    铁证：主人发「画张群友涩图，这是群友的参考图」+ 一张**聊天截图**（截图里是只小猪），
            #    反推出来 14 个框全是「聊天气泡 / 左侧方形头像 / 右上圆形头像 / 等级牌」，
            #    整张截图被当成画面主体 ⇒ 出一张聊天界面截图，当然不是他要的。
            #    现在两件事一起修：
            #      ① `what` 作为**画图要求**塞进视觉提示词，并明说「以要求为准」；
            #      ② 明确剔除**界面元素**（气泡/头像/等级牌/按钮/边框/水印/鼠标箭头），
            #         这些是截图自带的东西，不是要画的内容。
            _vc = _moss_vision_call
            _want = ""
            if raw:
                # 🔴 2026-10-31 第三处（主人「你如果不知道这是啥，肯定要先知道这个东西是什么，
                #    你才能继续画呀」）：**图生图分支原来完全不查证** —— 你发张图说
                #    「画个珂莱塔站在这个场景里」，bot 只照图复刻，根本不去查珂莱塔长什么样。
                #    文生图那条已经接了查证，图生图这条漏了。现在：要求里出现角色名/作品名
                #    ⇒ 先联网查官方外观，再一起塞进反推提示词（查到的外观优先于参考图）。
                _hint, _who = "", []
                if lookup:
                    _hint, _who = _moss_subject_hint(raw)
                    if _who:
                        who = _who
                        hint = _hint
                        log.info("Moss 图生图查证：%s（资料 %d 字）",
                                 "、".join(_who), len(_hint or ""))
                _want = ("\n\n【用户的画图要求，**以这个为准**】%s\n"
                         "参考图只用来判断「主体长什么样、怎么摆放」，"
                         "**不要复刻界面元素**：聊天气泡、头像、等级牌/徽章、按钮、"
                         "输入框、边框、水印、鼠标箭头、截图的黑边——这些都不是画面内容，"
                         "一个框都别给它们。画面主体要按用户要求来，而不是照抄整张截图。\n"
                         "`summary.caption` 也只写**要画的那幅画面**，"
                         "**不许写「一张截图 / 聊天界面 / 手机屏幕」这种描述**。"
                         % raw)
                if _hint:
                    _want += ("\n\n【查到的官方设定，外观**以这份为准**，"
                              "参考图里对不上的地方按这份改】\n" + _hint)

            def _vc(image_path, sys_prompt, user_text, _w=_want):
                return _moss_vision_call(image_path, sys_prompt + _w, user_text)
            # `focus=raw`：连 user 段一起约束（实测只加 system 段时，summary 还是写「聊天截图」）
            # 混合档（2026-11-02 主人指出 Studio 有三档）：hybrid = 先单独反推一次 Caption
            # 散文（动作/姿态理解更准），把散文喂给定位框那轮。caption 模式不产出布局框，
            # 在 bot 这条「要出图」的链上等价于 raw 直传，所以只在 layout/hybrid 间区分。
            # 2026-11-03：反推模式默认跟**配置**走（moss.prompt_mode），不是写死 layout。
            # 此前 hybrid 只在网页工具里生效，聊天/口令永远 layout —— 主人设了 hybrid 却看不到散文前置。
            _rm = str(reverse_mode or _moss_cfg().get("prompt_mode") or "layout").strip().lower()
            if _rm not in ("layout", "hybrid", "raw"):
                _rm = "layout"
            _prose = ""
            if _rm == "hybrid":
                _cap_ask = ("请用一段英文散文描述这张图：整体画面 + 每个元素的动作、姿态、"
                            "互动与光线。不要坐标、不要 JSON，只要一段连贯的描述。")
                try:
                    _prose = (_moss_vision_call(i2i, "你是图像描述助手。", _cap_ask) or "").strip()
                    log.info("Moss 混合反推：Caption 散文 %d 字", len(_prose))
                except Exception as e:  # noqa: BLE001
                    log.warning("Moss 混合反推：Caption 散文失败（%s），退回纯 layout", _short_err(e))
                    _prose = ""
            draft = moss_layout.from_image(i2i, w, h, _vc, cfg=mc, focus=raw, prose=_prose,
                                           detail=str(detail or "medium"))
            n = len(draft.get("items") or [])
            changed = 0
            if edit:
                draft, changed = moss_layout.edit_draft(
                    draft, edit, _moss_layout_llm(), cfg=mc)
                log.info("Moss 改图：改了 %d 处（共 %d 个框）", changed,
                         len(draft.get("items") or []))
            prompt, err = moss_layout.compile_draft(draft, cfg=mc)
            if not prompt:
                raise RuntimeError(err or "编译失败")
            log.info("Moss 提示词：反推 %d 个框 → %d 字%s", n, len(prompt),
                     ("（查了 %s）" % "、".join(who)) if who else "")
            return prompt, "", who
        if not raw:
            raise RuntimeError("没有描述也没有参考图")
        ask = raw
        if lookup:
            hint, who = _moss_subject_hint(raw)
            if hint:
                ask = (raw + "\n\n" + hint +
                       "\n\n（严格按上面的官方外观画：发色、瞳色、服装、配饰都要对上，"
                       "别自己换颜色或改成别的角色）")
        draft = moss_layout.from_text(ask, w, h, _moss_layout_llm(), cfg=mc, brief=brief)
        prompt, err = moss_layout.compile_draft(draft, cfg=mc)
        if not prompt:
            raise RuntimeError(err or "编译失败")
        n = len(draft.get("items") or [])
        log.info("Moss 提示词：%d 个框 → %d 字%s", n, len(prompt),
                 ("（查了 %s）" % "、".join(who)) if who else "")
        return prompt, "", who
    except Exception as e:
        # 🔴 图生图：反推没成 ⇒ 不许拿「用户那行字」凑一张出来（参考图会全丢）
        if i2i:
            raise RuntimeError("没看懂这张参考图，不敢照瞎画（反推失败：%s）"
                               % _short_err(e))
        log.warning("Moss 布局提示词失败（改走英文翻译兜底）：%s", _short_err(e))
    # 布局挂了：翻成英文再发，别把中文口令原样丢给 Moss
    alt = _moss_translate_prompt(raw, hint)
    if not alt:
        raise RuntimeError("提示词这步没成（%s），不敢拿原话硬画" % _short_err(
            "布局与翻译都失败"))
    return alt, "", who


def comfy_enabled():
    if comfy_gen is None or not (_comfy_cfg().get("enabled", True)):
        return False
    return True if _draw_switch is None else bool(_draw_switch)


def comfy_up(timeout=10):
    """服务在不在：问一次记 10 秒，省得 agent 连查把 /system_stats 打爆。"""
    now = time.time()
    if _draw_state["up"] is not None and now - _draw_state["up_t"] < 10:
        return _draw_state["up"]
    v = bool(comfy_gen and comfy_gen.up(_comfy_cfg(), timeout=timeout))
    _draw_state["up"], _draw_state["up_t"] = v, now
    return v


def comfy_info(gid=None):
    c = _comfy_cfg()
    with _draw_lock:
        left = max(0, int(float(c.get("cooldown_sec", 90)) - (time.time() - _draw_state["last"])))
        done = _draw_state["count"] if time.time() - _draw_state["since"] < 3600 else 0
    try:
        book = _subject_book()
        subj = {"lookup": bool(book), "cached": book.size() if book else 0}
    except Exception:
        subj = {"lookup": False, "cached": 0}
    # 2026-10-24：两条生图链路按群开关。gid 给了就报「这个群实际走哪条」，
    # 没给（状态接口）就报全局偏好，别把 None 露出去。
    _eng_here = draw_engine(gid) if gid is not None else None
    _pref = "moss" if str(c.get("engine") or "").lower().startswith("mos") else "comfyui"
    return {"enabled": comfy_enabled(), "server_up": comfy_up(),
            "drawing": _draw_state["busy"], "queued": _draw_q.qsize(),
            "cooldown_left_sec": left, "done_this_hour": done,
            "max_per_hour": c.get("max_per_hour"), "styles": list(_style_names()),
            "default_style": c.get("default_style"), "autostart": bool(c.get("autostart")),
            "engine": _eng_here or _pref,          # 显示用
            "engine_here": _eng_here,              # None = 这个群没开画图
            "engine_pref": _pref,                  # 两条都开时优先哪条
            "moss_ok": moss_enabled(gid),
            "moss_model": (_moss_cfg().get("model") or ""),
            "groups_comfyui": sorted(_draw_groups("comfyui")),
            "groups_moss": sorted(_draw_groups("moss")),
            "subject": subj}


DRAW_PREFIX_RE = re.compile(r"^(?:帮我[画绘]|给我[画绘]|请[画绘]|现画|画图|生图|生成图|生成一?张|来一张|"
                            r"快[画绘]|速涂|[画绘]一?[个张只幅条])\s*[:：]?\s*(\S.*)$")
DRAW_COLON_RE = re.compile(r"^(?:请|帮我|给我|你来)?(?:画|绘)[:：]\s*(\S.*)$")
# 「不查：描述」＝等同「画：描述」但跳过主体查证。必须带冒号，免得日常那句「别查了」被当成画图
DRAW_NOLOOK_PREFIX_RE = re.compile(r"^(?:不查|别查|不用查|不要查)[:：]\s*(\S.*)$")
# 逗号式档位剥完剩下的整句（「写实风景，中世纪塔楼」→「中世纪塔楼」）：没动词也当现画
HEAD_ONLY_RE = re.compile(r"^(\S.*)$", re.S)
DRAW_LOOSE_RE = re.compile(r"^(?:请|帮我|给我|你来|你)?(?:画|绘|生成|生)\s*(?:一张|1张|个|张|幅|只|下|出来)?"
                           r"[：:\s]*(\S.*)$")
# 「画面」「报错」这类正常话别被当成画图指令
DRAW_STOP_CHARS = "面廊笔报册板像作业风质匠工饼展行理题本纸布容制完"

# 🔴 2026-10-31：上面那串只拦得住「画面/画廊/画笔」这类**名词后缀**，
#    拦不住**动词补语 + 评价/疑问**，于是群里 @ 它说这些话都会真去出一张图：
#      画得挺像的 → what=「得挺像的」    生成好了吗 → what=「好了吗」
#      画不出来就算了 → what=「不出来就算了」  生图有点慢 → what=「有点慢」
#      你生成的啥呀 → what=「的啥呀」    画出这种效果我服了 → what=「出这种效果我服了」
#    后果是白烧 1~2 分钟显卡、还画出一张完全是垃圾的图。
#    判据用**短词表 + 结构**而不是语义，宁可漏不许误伤（误伤 = 用户真要画却没画）。
#      ⚠️ 别用「含吗就是疑问」这种粗规则：「帮我画个猫可以吗」是真请求，会被误拦。
_CMD_COMMENT_EQ = {"不错", "不错啊", "有点慢", "挺快", "一般", "还可以", "还行",
                   "挺好", "挺像", "好慢", "好快", "服了", "算了", "崩了", "卡了",
                   "离谱", "绝了", "好了吗", "完了吗", "牛"}
_CMD_COMMENT_IN = ("我服了", "太牛了", "牛啊", "没谁了", "笑死", "离谱了")
_CMD_FAIL_HEAD = ("不出来", "不出", "不好", "不行", "没画", "失败", "卡住",
                  "崩了", "卡了", "太丑", "画废")
_ASK_WORDS_RE = re.compile(r"(什么|啥|怎么|多久|多少)")
# 是/非限定疑问句：「生成 还是手绘好」「翻唱 要不要先its衣食父母」
_CMD_EITHER_RE = re.compile(r"^(?:还是|是不是|有没有|要不要|到底|能不能|能不能别)")
# fuzz 出来的评价/疑问尾巴（`tests/_probe_cmdfalse.py` 的那组非请求后缀），
# 直接接在命令关键词后面 ⇒ 全是"在评价这句话涉及的事"，不是要执行它。
_CMD_NOISE_TAIL = ("得真好", "得一般", "不太行", "有问题", "有点烦", "是啥呀",
                   "好了吗", "太慢了", "怎么回事", "我记得你说过", "不太对",
                   "有问题了", "不行了", "老毛病", "有点怪")


def _cmd_noise(what):
    """这句话看着像命令，其实是在**评价 / 发问 / 报状态** ⇒ 别当指令执行（2026-10-31）。

    起因是画图：`画得挺像的` 被当成画图指令真出了一张（§12.21）。后来做了全量排查
    （`tests/_probe_cmdfalse.py`：把每条命令的关键词接上「得真好 / 有问题 / 是啥呀…」
    这类**非请求后缀**去跑），发现**同款问题横跨 10 条命令** —— 最贵的是视频和翻唱：
        做视频有问题      → 真去生成半小时的视频
        翻唱不太行        → 真去搜歌 + 合成几分钟
    所以这个函数从「画图专用」升级成**通用闸**，贵命令的入口都要调它。

    判据用**结构 + 短词表**，不用语义理解（稳、可预测）：
    · 整句是短评价语（`_CMD_COMMENT_EQ`：不错 / 有点慢 / 好了吗…）
    · 含固定感叹（`_CMD_COMMENT_IN`：我服了 / 太牛了…）
    · 以失败/做不动开头（`_CMD_FAIL_HEAD`：不出来 / 卡住 / 画废…）
    · 含疑问代词（啥 / 什么 / 怎么 / 多久）
    · 是非限定 `还是/是不是/有没有` 这类**选择/确认疑问句**
    · 首字「得」= 标准动补评价句（画得挺像的）；首字「到」且 ≤6 字（画到一半卡了）
    · 以 `_CMD_NOISE_TAIL` 里的评价/疑问短语开头（**这份清单来自上面那个 fuzz，可以继续补**）

    ⚠️ **拿不准就返回 False（放行）**：拦错 = 主人真要画却没画，比偶尔白跑一张更糟。
    ⚠️ **别用「含吗=疑问」**：「帮我画个猫可以吗」是真请求，会被误伤。
    """
    s = str(what or "").strip()
    if not s:
        return False
    if s in _CMD_COMMENT_EQ:
        return True
    if any(w in s for w in _CMD_COMMENT_IN):
        return True
    if s.startswith(_CMD_FAIL_HEAD):
        return True
    if _ASK_WORDS_RE.search(s):
        return True
    if _CMD_EITHER_RE.search(s):
        return True
    if s[0] == "得":
        return True
    if s[0] == "到" and len(s) <= 6:
        return True
    if s.startswith(_CMD_NOISE_TAIL):
        return True
    return False
DRAW_CTL_WORDS = {"状态", "启动", "加载", "卸载", "清理", "开", "关", "on", "off", "帮助", "help",
                  "档", "档位", "style", "画图档"}
DRAW_EMPTY_WORDS = {"图", "图片", "一张图", "个图", "画", "一张", "一幅",
                    # 🔴 纯指代词（2026-11-06 私聊实测「画自己」「画你自己」直发垃圾图）：
                    #    指代词没有任何画面信息，云端文生图只能瞎编。不直发，
                    #    落主脑 —— 它知道「自己/你」指谁，能传 self=true 拿真长相。
                    "自己", "你自己", "我自己", "我", "你",
                    "本喵", "本人", "他", "她", "它", "这个人", "那人"}
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")
# 「快画/草图」= 同套 Krea2 但跳过 RTX 放大的轻量档；「横图/宽屏」= 宽高对调。这些词只当开关用，不进提示词
DRAW_FAST_RE = re.compile(r"(快画|快速画|快点画|速涂|草图|秒出|草稿档|快档|draft|fast)", re.I)
DRAW_LAND_RE = re.compile(r"(横图|横版|横屏|宽屏|landscape)", re.I)
# 长宽比标记：16:9 / 1:1 / 9:16 / 16x9 / 16比9 … 只认白名单里的常用比例，
# 免得「13:45」这种时间被误当成比例。2026-09-18 主人定：发画图指令可指定比例，默认 9:16。
DRAW_RATIO_RE = re.compile(r"(?<![\w.])([1-9]\d{0,2})\s*[:：xX×比]\s*([1-9]\d{0,2})(?![\w.])")
KNOWN_ASPECTS = {
    # 常用横/方
    "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "5:3", "3:5", "5:4", "4:5",
    "2:1", "1:2", "16:10", "10:16", "21:9", "9:21", "9:5", "5:9", "3:1", "1:3",
    "5:7", "7:5",
    # 2026-10-04 主人点名补的：**6:19 竖幅**（比9:16 更长，手机壁纸/短视频封面用）
    "6:19", "19:6", "4:9", "9:4", "2:9", "9:2", "10:19", "19:10", "7:16", "16:7",
    "5:11", "11:5", "3:7", "7:3", "8:16", "16:8", "1:4", "4:1", "2:5", "5:2",
    "3:8", "8:3", "11:16", "16:11", "20:9", "9:20", "1:3", "3:1", "5:10", "10:5",
}
ASPECT_DEFAULT = "9:16"

# 口语方向词 → 比例。主人 2026-10-04 点名：「宽屏/横屏/竖屏」这些口语要能识别。
# ⚠️ 这些词**必须从提示词里抠掉**（别让「横屏」混进 tag串）。
# 优先级：显式比例（9:16）> 方向词（横屏）；显式比例写了就以它为准。
ASPECT_ORIENT = {
    # 横 / 宽
    "横屏": "16:9", "横图": "16:9", "横版": "16:9", "宽屏": "16:9", "宽幅": "16:9",
    "横向": "16:9", "全景": "21:9", "超宽": "21:9", "宽银幕": "21:9",
    "方图": "1:1", "正方图": "1:1",
    "竖屏": "9:16", "竖图": "9:16", "竖版": "9:16", "竖幅": "9:16", "纵向": "9:16",
    "手机屏": "9:16", "手机壁纸": "9:16", "竖长": "6:19", "长竖屏": "6:19",
    "小横屏": "4:3", "小竖屏": "3:4",
}
# ⚠️ 词表瘦身（2026-10-04 探针实测）：**删掉歧义词**，否则会误伤正常描述 ——
#   「方形的盒子」曾被判成 1:1还把「方形」抠掉，剩「的盒子」污染画面；
#   英文 wide/tall 在 tag 串里太常见（a girl holding a wide hat）⇒ 一律删。
#   保留的词都是**明确的构图意图**，出现在画面描述里的概率极低。
# ⚠️ 长的词必须先匹配（"超宽" 先于 "宽屏"，"手机壁纸" 先于 "手机屏"，"长竖屏" 先于 "竖屏"）
ASPECT_ORIENT_RE = re.compile(
    "(" + "|".join(sorted((re.escape(k) for k in ASPECT_ORIENT), key=len, reverse=True)) + ")",
    re.I)
# 英文方向词单独一份：**要求独立成词**（前后不能是字母），
# 否则 "wide hat" / "tall building" 这种普通英文 tag 会被当方向指令。
ASPECT_ORIENT_EN = {
    "landscape": "16:9", "panoramic": "21:9", "cinemascope": "21:9",
    "square": "1:1", "portrait": "9:16",
}
ASPECT_ORIENT_EN_RE = re.compile(
    r"(?<![A-Za-z])(" +
    "|".join(sorted((re.escape(k) for k in ASPECT_ORIENT_EN), key=len, reverse=True)) +
    r")(?![A-Za-z])", re.I)


def extract_aspect(text):
    """从画图指令里挑「长宽比」。

    两路（2026-10-04 主人要求，口语也得认）：
      1) 显式比例：16:9 / 1:1 / 9:16 / 16x9 / 16比9 / 6:19 …（只认 KNOWN_ASPECTS，
         避免误伤「13:45」这类时间串）
      2) 口语方向：横屏 / 宽屏 / 竖屏 / 方图 / 超宽 / 手机壁纸 / 长竖屏 …（ASPECT_ORIENT）
         英文 landscape/portrait/square 走 ASPECT_ORIENT_EN（**必须独立成词**）
    **显式比例优先于方向词**；两路都命中时以显式为准。
    命中词一律从文案里抠掉 —— 方向词绝不能混进提示词（「横屏」进tag 串= 污染画面）。
    返回 (去掉标记的文案, 'a:b' 或 None)。"""
    if not text:
        return text, None
    ratio = None
    m = DRAW_RATIO_RE.search(text)
    if m:
        norm = "%s:%s" % (m.group(1), m.group(2))
        if norm in KNOWN_ASPECTS:
            ratio = norm
            text = (text[:m.start()] + " " + text[m.end():])
    # 方向词（显式比例已命中时也照抠，两种写法混着发不冲突）
    if _aspect_words_on():
        # 先中文（无边界问题，已按最长优先），再英文（要求独立成词）
        mo = ASPECT_ORIENT_RE.search(text)
        if mo:
            if ratio is None:
                ratio = ASPECT_ORIENT.get(mo.group(1).lower()) or ASPECT_ORIENT.get(mo.group(1))
            text = (text[:mo.start()] + " " + text[mo.end():])
        me = ASPECT_ORIENT_EN_RE.search(text)
        if me:
            if ratio is None:
                ratio = ASPECT_ORIENT_EN.get(me.group(1).lower())
            text = (text[:me.start()] + " " + text[me.end():])
    if ratio is None:
        return text, None
    text = re.sub(r"\s{2,}", " ", text).strip()
    # 清理残留的标点尾巴（「，16:9」「：横屏」）
    text = re.sub(r"^[，,、：:；;\s]+", "", text)
    text = re.sub(r"[，,、；;\s]+$", "", text)
    return text, ratio


def _image_aspect(path):
    """读本地图算出「最简整数比」长宽比（如 4000x6000 -> 2:3）。读不到返回 None。"""
    try:
        from PIL import Image
        with Image.open(str(path)) as im:
            w, h = im.size
    except Exception:
        return None
    w, h = int(w or 0), int(h or 0)
    if w <= 0 or h <= 0:
        return None
    import math
    g = math.gcd(w, h) or 1
    return "%d:%d" % (w // g, h // g)

DRAW_NOLOOK_RE = re.compile(r"(不查|别查|不用查|莫查|不要查|直接画|照我说的(?:画)?|就按我说的|no[- _]?lookup)", re.I)

COMFY_CMD_RE = re.compile(r"^(?:画图|生图|现画|comfy|comfyui)\s*(状态|开|关|on|off|启动|加载|卸载|清理|重查|帮助|help)?\s*$",
                          re.I)
# 「画图 档 krea2」换默认档（等价于「设置 画图档 krea2」）。必须带档位词，免得「画图 一只猫」被吞
COMFY_STYLE_RE = re.compile(r"^(?:画图|生图|现画|comfy|comfyui)\s*(?:档位?|style)\s*(\S*)\s*$", re.I)
# 「画图 引擎 moss|comfyui」切换生图链路（2026-10-24 新增第二条 Moss 云端链路）
COMFY_ENGINE_RE = re.compile(r"^(?:画图|生图|现画|comfy|comfyui)\s*(?:引擎|engine)\s*(\S*)\s*$", re.I)
# 「comfyui 开」「mossapi 关」——**按群**开关某一条生图链路（2026-10-24 主人定：
# 两条链路各自独立，选了开、没选关）。必须带 开/关，否则「comfyui」单独出现仍当状态查。
ENGINE_SWITCH_RE = re.compile(
    r"^(comfyui|comfy|mossapi|moss\s*api|moss|mosi)\s*(开|关|on|off)\s*$", re.I)


def _what_len_max():
    """画图描述长度上限（config comfyui.what_len_max，默认 2000）。

    2026-09-18 从 1000 提到 2000：danbooru 成品 tag 串动辄一两千字，1000 截不住，
    超了就整条掉出画图链路、被当聊天送进 AI 大脑（云端模型自己审一遍再拒答），
    主人反馈成"发 tag 还是先审核一遍说不给生成"。
    """
    # 2026-11-03 主人「文字上限越大越好」：引用 Moss 布局提示词（2k~4k 字）要能直接画，
    # 2000 会拦在「描述太长」上。提到 4000（config 可覆盖）。
    return int(CFG.get("comfyui", {}).get("what_len_max") or 4000)


def parse_draw(raw, addressed=False):
    """像现画指令就返回 (描述, 风格, 是否横图, 是否跳过查证)，否则 None。没@我时只认明确写法，避免误伤正常聊天。"""
    # 多行消息（手机上换行发的 tag 串）正则跨不过 \n 会整条漏掉，先归一成空格
    t = re.sub(r"\s+", " ", (raw or "")).strip().strip("【】()（）")
    t = re.sub(r"^\s*(?:@\S+[\s,，、]*)+", "", t)
    for n in bot_names():
        if n and t.lower().startswith(n.lower()):
            t = t[len(n):].lstrip(" \t,，、。!！~")
    t = t.strip()
    # 先挑长宽比标记（16:9/1:1/9:16…），从指令里抠掉，免得混进提示词；
    # 没明说比例时回 None（generate 里回落默认 9:16 / 画同款回落原图比例）。
    t, aspect = extract_aspect(t)
    # 长度闸分两层，别再合成一道（主人 2026-09-18 踩到）：
    #   硬闸 _HARD_MAX ——"这条根本不像画图指令"的兜底，超了才 None（掉回聊天判断）
    #   软闸 what_len_max ——"像是指令但描述太长"，回一句明确提示，**绝不静默掉进 AI 大脑**
    # 以前两层都是 1000：超长成品串整条被硬闸挡掉 → 云端模型先审一遍再拒答，
    # 主人看到的就是"我发 tag 还是被审核然后说不给生成"。
    what_len_max = _what_len_max()
    _HARD_MAX = max(4000, what_len_max * 2)
    if not (2 <= len(t) <= _HARD_MAX):
        return None
    m0 = (DRAW_PREFIX_RE.match(t) or DRAW_COLON_RE.match(t)
          or DRAW_NOLOOK_PREFIX_RE.match(t)
          or (DRAW_LOOSE_RE.match(t) if addressed else None))
    if m0 and len((m0.group(1) or "")) > what_len_max:
        return "draw_too_long"
    # 显式指定工作流/档位：「用anima画猫」「画[anima]猫」「画猫 用krea2档」。
    # 档名可以是 config styles / 工作流库里的真名，也吃 anima/krea2/fast 这类简写；
    # 认不出档名的括号不当指令处理，避免误伤正文。
    style = None

    # 显式指定工作流/档位：「用anima画猫」「画[anima]猫」「画猫 用krea2档」
    # （词表与剥离手法和画同款共用一份 —— strip_style_prefix）
    style, t, _sform = strip_style_prefix_ex(t, addressed=addressed)
    m = (DRAW_PREFIX_RE.match(t) or DRAW_COLON_RE.match(t)
         or DRAW_NOLOOK_PREFIX_RE.match(t)
         or (DRAW_LOOSE_RE.match(t) if addressed else None))
    if not m and _sform == "comma" and style:
        # 「写实风景，中世纪高耸塔楼，尖顶，石头墙，阴影层次」——整句没动词，档位词本身
        # 就是指令。剥完只剩描述，DRAW_* 全不认，所以在这里补一步直接当现画
        # （2026-09-19 主人踩到：这句被当聊天，回的是「我教你怎么画风景」）。描述清洗复用下面同一套。
        m = HEAD_ONLY_RE.match(t)
    if not m:
        # ---- 成品 tag 串 / 纯英文「画…」：直接进工作流，别送去给 AI 审查 ----
        # 主人 2026-09-18 定：自己写好的 tag 就是给工作流的，不是聊天内容。以前这类
        # 消息（「画explicit, a smartphone screenshot…」）压根没进现画链路，掉进 AI 大脑
        # 被云端模型先审一遍再拒答（"我无法满足你的要求"）——跟 09-13 撤掉审核闸的定案矛盾。
        # 认法两条：① 内容是成品 tag 串（looks_like_tagline）→ 认；
        #          ② 写了「画/绘」动词且整句无中文（不可能是中文闲聊）→ 认。
        # 裸串（没写「画」）只在被@/私聊时认，群里别人贴串别替他开显卡。
        loose = DRAW_LOOSE_RE.match(t)
        body = ((loose.group(1) or "").strip() if loose else t.strip())
        if comfy_gen is not None and body:
            tagline = False
            try:
                tagline = bool(comfy_gen.looks_like_tagline(body))
            except Exception:
                tagline = False
            if tagline and (loose or addressed):
                return body, (style or None), bool(DRAW_LAND_RE.search(t)), True, aspect
            if loose and not _CJK_RE.search(t) and len(body) >= 2 \
                    and body[0] not in DRAW_STOP_CHARS:
                # 纯英文内容里没有中文专有名词，查证也是白跑一步 —— 一起跳过
                return body, (style or None), bool(DRAW_LAND_RE.search(t)), True, aspect
        return None
    # 开关词在整句上认：「快画」会被前缀正则吃掉，只剩描述就看不见了
    fast = bool(DRAW_FAST_RE.search(t))
    land = bool(DRAW_LAND_RE.search(t))
    nolook = bool(DRAW_NOLOOK_RE.search(t)) or bool(DRAW_NOLOOK_PREFIX_RE.match(t))
    what = (m.group(1) or "").strip(" ，。~！!、")
    what = re.sub(r"^(?:一个|一张|一只|一幅|个|张|只|幅|条|片)\s*", "", what).strip()
    what = DRAW_LAND_RE.sub("", what)
    what = re.sub(r"[:：，,、\s]*(?:不查|别查|不用查|莫查|不要查|直接画|照我说的(?:画)?|"
                  r"就按我说的|no[- _]?lookup)[:：，,、\s]*", " ", what, flags=re.I)
    # 🔴 2026-10-31：把「这张是什么」的**说明尾巴**剔掉。
    #    主人发「@柚大人 画张群友涩图，这是群友的参考图」+ 一张图，
    #    bot 的受理话术回的是「画「群友涩图，这是群友的参考图」」——
    #    后半句是在**告诉我这张图是什么**，不是要画的内容，却被当成提示词一起送去布局，
    #    于是布局模型老老实实画「一张写着群友涩图、附着一张参考图的画面」。
    #    判据收窄成两类（`parse_draw` 拿不到「这条消息带没带图」，所以宁可窄）：
    #      ① 「这是 X 的参考图/原图/素材/例图」——「的」前面是**归属人**，一定是说明参考图；
    #      ② 「这是参考图/原图」（无归属，光秃秃三个字，同义）。
    #    ⚠️ **不删**「这是我家猫」「这是一个图」这类——那可能是画面内容（要画的猫是自家的）。
    #    ⚠️ 只在**尾巴**位置删（`$`），别动正文里正常出现的「这是」。
    what = re.sub(
        r"[，,。;；]?\s*(?:这|那|上面|下图|上图)?(?:是|为|就是|指的?是)"
        r"(?:我|他|她|群友|朋友|网友|\S{0,6})的(?:参考图|原图|素材图|例图|参考照片|图片|照片|图)"
        r"(?:啊|呀|吧|哦)?\s*$", "", what).strip()
    what = re.sub(
        r"[，,。;；]?\s*(?:这|那|上面)?(?:是|为|就是)(?:参考图|原图|素材图|例图)"
        r"(?:啊|呀|吧|哦)?\s*$", "", what).strip()
    what = re.sub(r"[，,、\s]+$", "", what).strip()
    # 🔴 2026-10-31 加：评价/疑问/报状态的那类话别拿来出图（判据见 `_cmd_noise`）
    if not re.search(r"[\w\u4e00-\u9fff]", what) or what[0] in DRAW_STOP_CHARS \
            or what.lower() in DRAW_CTL_WORDS or what in DRAW_EMPTY_WORDS \
            or _cmd_noise(what):
        return None
    # 2026-10-29 主人晒图：「画 [吧]」被当成画图指令，拿「[吧]」当提示词去云端画了一张。
    # 剥掉包裹用的括号/标点后**全是**语气词的（吧/吗/呢/嘛…）绝不是画图指令。
    # ⚠️ 只卡 ≤2 字的整粒语气词，别误伤「画一只猫吧」（剥完「猫吧」，还剩「猫」）。
    if not _draw_desc_ok(what):
        return None
    # 描述里没有中文 → 没有中文专有名词可查，查证那步纯属白跑（主人 2026-09-18：
    # 英文串要的是"直接进工作流"，中间任何一步模型介入都算多余）
    if not _CJK_RE.search(what):
        nolook = True
    # 档位词落在描述里（写法不在五式内）→ 静默走默认档，只在日志留痕。
    # 2026-09-19 加：主人踩到「画写实风景，中世纪卢浮宫」被当成描述 + 默认档 anima，
    # 出图档位错了却看不出来。不回话打扰群友，但日志里必须能查到。
    if not style:
        _head = re.split(r"[，,、；;：:\s]", t.strip(), 1)[0]
        if _is_style_word(_head):
            log.info("档位词「%s」落在描述里没进档位（写法不在五式内），本次走默认档 %s",
                     _head, _comfy_cfg().get("default_style"))
    return what, (style or ("fast" if fast else None)), land, nolook, aspect


def draw_gate(gid, privileged, engine=None):
    """四道闸：**这个群开没开** / 谁能画 / 间隔 / 每小时上限 / 队列深度。

    2026-10-24 主人改：两条链路**各自按群开关**（选了开、没选关），所以第一道闸是
    「这个群有没有开画图」，其次才是链路自己的前置条件：
      · comfyui：模块在 + 「画图 开」+ 服务在跑（本地显卡那套）
      · moss：只要配了 Key 就行（云端算力，不用开画图开关、不用预热）
    间隔/上限/队列三闸两条路共用，免得被刷。

    engine 给了就强制验那条（「moss反推」要跳过偏好、直接走云端）。
    """
    c = _comfy_cfg()
    eng = engine or draw_engine(gid)
    if eng is None:
        return "这个群没开画图。管理员发「comfyui 开」或「mossapi 开」才能用。"
    if eng == "moss":
        if not moss_enabled(gid):
            return "云端生图没配好（config.json 的 moss.api_key 是空的，或 moss_gen 模块没加载）。"
    else:
        if comfy_gen is None:
            return "现画模块没加载成功，画不了。"
        if not comfy_enabled():
            return "现画功能关着呢（让群管理发「画图 开」）。"
        if not comfy_up():
            if not c.get("autostart"):
                return "本地 ComfyUI 现在没在跑，先让管理员发「画图 启动」。"
    if not c.get("members_allowed", True) and not privileged:
        return "这个功能只对群管理员开放。"
    now = time.time()
    with _draw_lock:
        if now - _draw_state["since"] > 3600:
            _draw_state["since"], _draw_state["count"] = now, 0
        cd = float(c.get("cooldown_sec", 90))
        if now - _draw_state["last"] < cd:
            return "上一张才画完，等 %d 秒再来（间隔 %d 秒，显卡歇口气）。" % (
                int(_draw_state["last"] + cd - now), int(cd))
        if _draw_state["count"] >= int(c.get("max_per_hour", 10)):
            return "这一小时已经画了 %d 张了，明天再说吧。" % _draw_state["count"]
        depth = _draw_q.qsize() + (1 if _draw_state["busy"] else 0)
        if depth >= int(c.get("queue_max", 3)):
            return "前面还排着 %d 个，先不接了。" % depth
        _draw_state["count"] += 1
    return None


def _is_moss_json_prompt(text):
    """这段文字是不是已经编译好的 Moss 提示词（JSON）。

    2026-11-03 主人引用自己那条 Moss 布局提示词说「画个」：这串是**成品**，
    再走一遍 `_moss_prompt` 会被二次改写/重新布局，画出来就不是原来那张。
    识别到就直接交给 moss_gen，跳过布局。
    """
    t = str(text or "").strip()
    if not t.startswith("{"):
        return False
    try:
        # raw_decode 只吃开头那个 JSON 对象，后面的说明文字（引用多条时会有）不影响
        d, _ = json.JSONDecoder().raw_decode(t)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(d, dict) and any(
        k in d for k in ("summary", "canvas", "items", "layout"))


def draw_submit(gid, what, style, who, mid=0, privileged=False, landscape=False,
                lookup=True, i2i=None, denoise=None, aspect=None, engine=None,
                edit=None, brief=False, reverse_mode=None, detail=None, raw_prompt=False,
                return_jid=False):
    """排队一张现画任务；默认返回 (是否受理, 说明或拒绝理由)。

    return_jid=True 时返回 (是否受理, 说明, jid)，供主脑读取异步任务终态；
    所有旧调用点不传这个参数，返回形状不变。

    i2i 给了本地参考图路径就走图生图（先传到 ComfyUI 当参考图，denoise 控相似度）。
    aspect = '16:9'/'1:1'/'9:16' 等显式长宽比；None 表示用默认（文生图 9:16，
    画同款回落原图比例）。

    engine 给了就**强制走那条链路**（「moss反推」用：跳过偏好、直接云端）。
    """
    # 🔴 2026-10-29 修「comfyui / moss 两条线路乱套」（主人当天报的三条里最要命的一条）：
    #    以前这里 engine 留空 ⇒ 落到 `draw_engine(gid)` 的**全局偏好** `comfyui.engine`。
    #    主人偏好是 moss，于是「/厚涂图生图」「画图 厚涂 xxx」全被送去云端 Moss ——
    #    厚涂/krea2/anima/宫格 这些档位在那边根本不存在，等于**命令被静默丢弃**，
    #    出来的是一张风格毫不相干、跟参考图也没关系的 Moss 图（主人：「我发一张图，
    #    都没说让他画，也直接拿去 moss 画一张给我」）。
    #    现在：显式指定了本地档 → 强制走 ComfyUI；这个会话没开本地链路就**明说**，
    #    绝不悄悄改成云端出图（静默改道比报错更坑人）。
    # 成品 Moss JSON 只可能喂给云端；没显式指定链路时落 moss（是否可用交给 draw_gate 判）。
    def _ret(ok, note="", jid=None):
        return (ok, note, jid) if return_jid else (ok, note)
    if raw_prompt and engine is None:
        engine = "moss"
    if engine is None and style:
        _le = style_local_engine(style)
        if _le:
            if engine_on_here(gid, "comfyui"):
                engine = "comfyui"
            elif engine_on_here(gid, "moss"):
                return _ret(False, "「%s」是本地档（ComfyUI 工作流），云端 Moss 没有这个风格。"
                                  "这个会话只开了云端——管理员发「comfyui 开」再试。" % style)
    reason = draw_gate(gid, privileged, engine=engine)
    if reason:
        return _ret(False, reason)
    with _draw_lock:
        _draw_state["seq"] += 1
        jid = _draw_state["seq"]
    _job = {"id": jid, "gid": int(gid), "what": what, "style": style,
            "who": who, "mid": mid, "landscape": bool(landscape),
            "lookup": bool(lookup), "i2i": (str(i2i) if i2i else None),
            "denoise": denoise, "aspect": (str(aspect) if aspect else None),
            "engine": engine, "edit": (str(edit) if edit else None),
            "brief": bool(brief),
            "reverse_mode": (str(reverse_mode) if reverse_mode else None),
            "detail": (str(detail) if detail else None),
            "raw_prompt": bool(raw_prompt)}
    _draw_result_set(jid, status="queued", done=False, gid=int(gid),
                     engine=(engine or draw_engine(gid)), style=style,
                     i2i=bool(i2i), error="")
    _draw_q.put(_job)
    # 2026-11-02 阶段2 采集：受理参数明细（引擎/简易档/改稿/反推模式/详细程度）
    log.info("现画受理 gid=%s by=%s engine=%s brief=%s edit=%s reverse_mode=%s detail=%s raw_prompt=%s what=%s",
             gid, who or "-", engine or "-", bool(brief), bool(edit),
             reverse_mode or "-", detail or "-", bool(raw_prompt), str(what)[:40])
    return _ret(True, "", jid)


DRAW_TIME_FILE = STATE / "draw_time.json"
DRAW_TIME_KEEP = 6            # 每档只留最近 6 次实测
_draw_est_lock = threading.Lock()


def _draw_times():
    try:
        d = json.loads(DRAW_TIME_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _est_learn(style, secs):
    """把这一张的真实耗时记下来，下一张「大概 N 秒」按最近实测报（2026-10-29 加）。

    为什么必须学：主人实测报「大概 110 秒」、实际 **252 秒**（2304×2304 厚涂档）。
    写死的 `est_sec` 只在机器空着、图不大的时候准；图一放大、显存一换页就差一倍多。
    报不准的代价是主人干等两分钟还以为是卡死了 —— 所以拿实测中位数校正。
    """
    try:
        s = int(float(secs))
    except (TypeError, ValueError):
        return
    if s <= 5 or s > 3600:        # 明显不是正常出图耗时（秒退/离谱），别污染样本
        return
    k = str(style or "默认")
    with _draw_est_lock:
        d = _draw_times()
        try:
            arr = [int(x) for x in (d.get(k) or [])]
        except (TypeError, ValueError):
            arr = []
        d[k] = (arr + [s])[-DRAW_TIME_KEEP:]
        try:
            DRAW_TIME_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.debug("记出图耗时失败: %s", e)


def _est_stats(style):
    """最近实测的中位数；样本 < 2 返回 None（单次的抖动不能当结论）。"""
    try:
        arr = sorted(int(x) for x in (_draw_times().get(str(style or "默认")) or [])
                     if isinstance(x, (int, float)) and x > 0)
    except (TypeError, ValueError):
        return None
    n = len(arr)
    if n < 2:
        return None
    return int(arr[n // 2]) if n % 2 else int(round((arr[n // 2 - 1] + arr[n // 2]) / 2.0))


def _draw_est(style=None):
    """这张大概多久：(热机, 冷机)。**优先按最近实测中位数**，没样本才用配置里的 est_sec。"""
    warm = cold = None
    if comfy_gen:
        try:
            warm, cold = comfy_gen.est_seconds(_comfy_cfg(), style)
        except Exception:
            pass
    if warm is None:
        warm, cold = 60, 200
    real = _est_stats(style)
    if real:
        warm, cold = real, int(real * 1.35 + 30)   # 冷机还要加载模型，按实测上浮
    return int(warm), int(cold)


def _draw_llm(style=None):
    c = _comfy_cfg()
    if not c.get("enhance", True):
        return None
    styles = c.get("styles") or {}
    s = styles.get(style or c.get("default_style") or "krea2") or {}
    # 档位可以自己关润色（2026-10-16 宫格档：扩写交给工作流里的两级 PromptExpand，
    # bot 再润一道会把「一句话」扩成别的味道，还白多跑一次 LLM）
    if s.get("enhance", True) is False:
        return None
    name = s.get("enhance_provider") or c.get("enhance_provider") or "zhipu_fast"

    def _call(messages):
        return chat_once(name, messages)
    return _call


def _draw_webllm(style=None):
    """查设定用的那张嘴：先联网搜资料，再交给小模型压成 WHO/LOOK/TAGS 卡片。

    **文生图的查设定**和**图生图认人后的查证**都走这一个函数。
    """
    if comfy_gen is None:
        return None
    c = _comfy_cfg()
    if not c.get("enhance", True):
        return None
    styles = c.get("styles") or {}
    s = styles.get(style or c.get("default_style") or "krea2") or {}
    name = s.get("subject_provider") or c.get("subject_provider") \
        or s.get("enhance_provider") or c.get("enhance_provider") or "zhipu_fast"
    cap = float(c.get("subject_timeout_sec", 30))

    def _call(messages, query=""):
        snips = web_search_any(query, count=int(c.get("subject_search_count", 6))) if query else ""
        msgs = list(messages)
        if snips:
            msgs.append({"role": "user", "content": "【联网查到的资料，以它为准】\n" + snips})
        return chat_once(name, msgs, max_timeout=cap)
    return _call


_subject_book_inst = None


def _subject_book():
    """设定查证本（带缓存）。关了就返回 None，出图照旧。"""
    global _subject_book_inst
    c = _comfy_cfg()
    if comfy_gen is None or not c.get("subject_lookup", True):
        return None
    if _subject_book_inst is None:
        _subject_book_inst = comfy_gen.SubjectBook(
            path=STATE / "subjects.json",
            enabled=True,
            max_names=c.get("subject_max_names", 2),
            budget_sec=c.get("subject_budget_sec", 45))
    return _subject_book_inst


def _rm_file(p):
    """删一个文件，被 NapCat 占用就等 1 秒重试一次。True=已删（含本来就不存在）。"""
    p = str(p)
    for i in (0, 1):
        try:
            os.remove(p)
            log.info("发后删图：已删 %s", p)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if i == 0:
                time.sleep(1.0)
        except Exception as e:
            log.warning("发后删图失败：%s：%s", p, e)
            return False
    log.warning("发后删图失败（文件被占用）：%s", p)
    return False


def _moss_safety_rewrite():
    """被上游 403 安全策略拦下时，把提示词改写成「清水版」的回调（给 moss_gen.generate 用）。

    🔴 2026-10-31 主人当场撞的：
       「@柚大人 画张群友涩图，这是群友的参考图」→ 群里只看到
       「上游 403：safety policy error」。
       实测确认 MOSI 对 `revealing clothing / suggestive pose` 这类**正向描述**必拦
       （同样结构换成「三个女孩站公园」正常出图）。上游策略改不了，但**图不该就这么丢**：
       这里让 LLM 把同一张画面改写成不带成人向词的版本 —— **主体、构图、配色、镜头
       全部保留**，只把「涩」「露」「暗示性姿势」这些拿掉，然后重画一次。
       成图结果不假装画出了原要求。
    """
    def _rewrite(prompt):
        sys_p = ("你是内容合规改写助手。用户给一段英文图像提示词，上游安全策略拒绝了它。"
                 "请把它改写成**同样构图、同样主体、同样配色与光线**，但**不含任何成人向"
                 "内容**的版本：去掉 nudity / revealing / suggestive / seductive / lingerie / "
                 "cleavage / panties / bare skin 这类描述与其暗示，改成得体的日常服装"
                 "（如 casual dress, sweater, jeans, school uniform）。"
                 "**必须保持**：几个人、谁是谁、姿势大致相同、视角、背景、色调、风格。"
                 "只输出改写后的那一段英文提示词：不要解释、不要中文、不要引号、不要 Markdown。")
        txt = _moss_layout_llm()(
            [{"role": "system", "content": sys_p},
             {"role": "user", "content": "原提示词：\n" + str(prompt)[:2500]}])
        txt = (txt or "").strip().strip("`").strip().strip('"')
        if not txt or not re.search(r"[A-Za-z]{3}", txt):
            return ""
        log.info("Moss 安全改写：%d 字 → %d 字（去掉成人向描述后重画）", len(prompt), len(txt))
        return " ".join(txt.split())[:1500]
    return _rewrite


def _delete_drawn_file(cfg, path, res=None):
    """现画发出去就把本地图删掉（旋钮「发后删图」，2026-09-13 主人定）。
    两处都删：bot 自己 images/ 里那份 + ComfyUI output 里的原图（2026-09-14 主人要求）。
    发送失败不删（主人还能去 images/ 里捞）；删不掉只记日志不报错——图反正已经发出去了。"""
    if not cfg.get("delete_after_send", True):
        return
    _rm_file(path)
    try:
        for p in comfy_gen.output_paths(cfg, res):
            _rm_file(p)
    except Exception as e:
        log.warning("清 ComfyUI output 原图失败：%s", e)


def do_draw(job):
    c = _comfy_cfg()
    gid, what = job["gid"], job["what"]
    style = job.get("style")
    eng = job.get("engine") or draw_engine(gid)   # 「moss反推」会强制指定链路
    try:
        if eng == "moss":
            # 云端链路：先过一遍 Studio 那套提示词工程
            #   · 文生图：一句话 → 整画面 Layout（框+描述）→ 编译成 Moss JSON 提示词
            #   · 画同款：参考图 → 视觉模型反推定位框 → 同上
            # 失败会自己回落成原始描述，不会因为布局这步把图搞没（见 _moss_prompt）。
            _asp = job.get("aspect")
            if job.get("i2i") and not _asp:
                _asp = _image_aspect(job["i2i"])      # 画同款跟着参考图的比例走
                log.info("Moss 画同款：参考图比例 %s", _asp or "(读不出，用默认)")
            if job.get("raw_prompt"):
                # 已经是编译好的 Moss JSON（用户引用的那条成品提示词）：
                # 再走一遍布局会二次改写，直接原样出图。
                prompt_text, note, _who = what, "", ""
                log.info("Moss 直出：已是成品提示词，跳过布局（%d 字）", len(str(what)))
            else:
                prompt_text, note, _who = _moss_prompt(what, _asp, job.get("i2i"),
                                                     lookup=job.get("lookup", True),
                                                     edit=job.get("edit"),
                                                     brief=job.get("brief", False),
                                                     reverse_mode=job.get("reverse_mode"),
                                                     detail=job.get("detail") or "medium")
            res = moss_gen.generate(
                _moss_cfg(), prompt_text,
                aspect=_asp,
                out_dir=image_folder(),
                # 被上游安全策略拦（403）时，改写成清水版再画一次（见 _moss_safety_rewrite）
                safety_retry=_moss_safety_rewrite())
            if note:
                res["note"] = note
            if _who:
                res["subject"] = _who       # 群里会说「顺手查了「珂莱塔」的设定再画的」
        else:
            if not comfy_up():
                if not c.get("autostart"):
                    raise comfy_gen.ComfyError("服务没开，管理员可发「画图 启动」")
                if not comfy_gen.boot(c):
                    raise comfy_gen.ComfyError("服务拉不起来")
            _draw_state["up_t"] = 0.0
            res = comfy_gen.generate(c, what, style, llm=_draw_llm(style),
                                     out_dir=image_folder(),
                                     landscape=job.get("landscape"),
                                     aspect=job.get("aspect"),
                                     book=_subject_book(),
                                     # 图生图不做联网查证（认人回落视觉模型链），
                                     # 联网查证只用于文生图的设定。
                                     web_llm=(None if job.get("i2i") else _draw_webllm(style)),
                                     lookup=job.get("lookup", True),
                                     image=job.get("i2i"), denoise=job.get("denoise"))
    except Exception as e:
        log.warning("现画失败 group=%s engine=%s: %s", gid, eng, e)
        _draw_result_set(job.get("id"), status="failed", done=True,
                         gid=int(gid), engine=eng, style=style,
                         error=_short_err(str(e)))
        with _draw_lock:
            _draw_state["last"] = time.time()   # 失败也计入冷却，别让坏服务被刷
        return None
    with _draw_lock:
        _draw_state["last"] = time.time()
        _draw_state["up_t"] = 0.0
    secs = int(res["seconds"])
    if eng != "moss":                 # 云端很快、本地慢，两套耗时不能混着算
        _est_learn(style, secs)
    sent = send_drawn(gid, res["file"], job.get("mid") or 0)
    _draw_result_set(job.get("id"), status="done" if sent else "failed", done=True,
                     gid=int(gid), engine=eng or res.get("engine"),
                     style=res.get("style") or style,
                     seconds=secs, sent=bool(sent),
                     error="" if sent else "出图了但图片没发出去")
    if sent:
        _delete_drawn_file(c, res["file"], res)
    log.info("现画完成 group=%s -> %s（%s/%s）", gid, res["file"], res.get("engine"), res.get("style"))
    return res


def send_drawn(gid, path, reply_to=0):
    """只发送工具产物；用户可见文字由主脑出口负责。"""
    segs = []
    if reply_to:
        segs.append({"type": "reply", "data": {"id": int(reply_to)}})
    segs.append({"type": "image", "data": {"file": str(path)}})
    ok = send_segments(int(gid), segs)
    if not ok and reply_to:
        ok = send_segments(int(gid), [s for s in segs if s["type"] != "reply"])
    return ok


def draw_worker():
    while True:
        job = _draw_q.get()
        _draw_state["busy"] = True
        try:
            _draw_result_set(job.get("id"), status="running", done=False)
            do_draw(job)
        except Exception as e:
            # worker 层异常也要带会话事实：只写 error 不带 gid 的话，
            # 按会话查 last_draw 就查不到这次失败（2026-11-07 审阅）。
            _draw_result_set(job.get("id"), status="failed", done=True,
                             gid=int(job.get("gid") or 0),
                             engine=job.get("engine") or draw_engine(job.get("gid") or 0),
                             style=job.get("style"),
                             error=_short_err(str(e)))
            log.exception("现画任务异常")
        finally:
            _draw_state["busy"] = False
            _draw_q.task_done()


def handle_comfy_cmd(gid, raw, privileged):
    global _draw_switch
    m = COMFY_CMD_RE.match(raw.strip())
    arg = (m.group(1) or "").strip().lower() if m else ""
    c = _comfy_cfg()
    need_admin = "这个要管理员权限。"

    if arg in ("状态", ""):
        i = comfy_info(gid)
        warm, cold = _draw_est(i.get("default_style"))
        _eng = i.get("engine_here")
        _on_c, _on_m = engine_on(gid, "comfyui"), engine_on(gid, "moss")
        _head = "本群：comfyui %s / mossapi %s → %s\n" % (
            "开" if _on_c else "关", "开" if _on_m else "关",
            ("走 " + ("云端 Moss" if _eng == "moss" else "本地 ComfyUI")) if _eng
            else "两条都关着，画不了（发「comfyui 开」或「mossapi 开」）")
        if _eng == "moss":
            _eng_line = _head + "云端 Moss（%s，%s）—— 不用「画图 开」，直接能画\n" % (
                i.get("moss_model") or "moss-image-sft-1.0",
                "Key 已配" if i.get("moss_ok") else "!! Key 没配，画不了")
            _srv = "本地 ComfyUI 状态：%s（走云端时不影响出图）" % ("在线" if i["server_up"] else "没开")
        else:
            _eng_line = _head + ("本地 ComfyUI（要「画图 开」才让用）\n" if _eng else "")
            _srv = None
        send_group(gid, _eng_line + "现画：%s（服务%s，正在画%s，排队%d）\n当前档「%s」约 %ds/张 / 冷却剩 %ds / 本小时已画 %d(上限%s)\n可用档：%s（换档：画图 档 名字）" % (
            "开" if i["enabled"] else "关", "在线" if i["server_up"] else "没开",
            "有" if i["drawing"] else "没", i["queued"], i["default_style"], warm,
            i["cooldown_left_sec"], i["done_this_hour"], i["max_per_hour"],
            "、".join(i["styles"])))
        if _srv:
            send_group(gid, _srv)
        elif _eng == "comfyui" and not i["server_up"]:
            send_group(gid, "本地 ComfyUI 没在跑。管理员发「画图 启动」我来拉起（首次加载要 1~2 分钟）。")
        return
    if arg in ("帮助", "help"):
        _d, _f = _draw_est(None)[0], _draw_est("fast")[0]
        _ds = _comfy_cfg().get("default_style") or "krea2"
        # 比例说明（含**当前默认值**，主人在网页上改了这里跟着变）—— 先拼好再传进 send_group
        _da = _comfy_cfg().get("default_aspect") or "9:16"
        _asp_help = (
            "比例：描述里写「6:19 / 9:16 / 3:4 / 1:1 / 16:9 / 21:9」这类比例就按它出图；"
            "也认口语——横屏/宽屏/横向/超宽出 16:9 或 21:9，竖屏/手机壁纸/竖幅出 9:16，"
            "方图出 1:1，长竖屏出 6:19，小横屏/小竖屏出 4:3 / 3:4。"
            "口语词只在这次这张图生效、不会混进画面。不写比例时按网页「默认比例」走（当前 " + _da + "）。"
            "例：「画 6:19 的风景」「画同款 1:1」「横屏 画个机器人」「方图 一只猫」。\n"
        )
        send_group(gid, "现画：「画：描述」「画图 描述」「帮我画个xx」，默认走「%s」档（约 %d 秒一张）；「随机画一张」我自己随机编提示词直接画（优先本地模型）；"
                        "「快画：描述」走约 %d 秒的轻量档。\n"
                        + _asp_help +
                        "⚡ 懒得@我就用「/」：「/画 一只猫」跟@我一样，所有画图口令都认"
                        "（「/画同款」「/视频 …」「/反推」「/随机画一张」）；"
                        "**直接指定档位**更短：「/宫格 描述」「/动画 描述」「/写实 描述」「/厚涂 描述」。\n"
                        # 2026-10-18 主人定：带图（本条带的 / 回复别人那条的）就当图生图
                        "⚡ 档位后面**带上或回复一张图**＝照着那张画（图生图），不用写「画同款」。\n"
                        "临时换档：「用Anima工作流画 描述」「画[anima]描述」「画 描述 用krea2档」都行，"
                        "可用档：%s。\n"
                        "anima 档的 @画师串会按画面主题自动换（管理员可在设置里关）；"
                        "描述里有角色名、历史人物、梗的，我先联网查设定再画（多花几秒，查过就记住）；"
                        "不想让我查就发「不查：描述」。\n"
                        "管理：画图 状态/启动/卸载/重查/开/关，换默认档「画图 档 krea2|Anima工作流」；"
                        "所有开关一张表——发「设置」。" % (_ds, _d, _f, "、".join(_style_names())))
        return
    if arg in ("开", "on", "关", "off"):
        if not privileged:
            send_group(gid, need_admin)
            return
        on = arg in ("开", "on")
        # 2026-10-24：生图改成「两条链路各自按群开关」。这条老口令保持手感 ——
        # 「画图 开/关」= 把这个群的**两条链路一起**开/关（顺便把全局总闸打开，
        # 免得按群开了却被总闸挡着，白高兴一场）。
        if on:
            knob_set("现画", "开", privileged=True, ctx={"gid": gid})
        draw_set_group_engine(gid, "comfyui", on, src="qq")
        draw_set_group_engine(gid, "moss", on, src="qq")
        send_group(gid, "本群画图已%s：comfyui %s / mossapi %s。%s" % (
            "打开" if on else "关闭",
            "开" if on else "关", "开" if on else "关",
            "" if on else "只发图库里现成的图。"))
        return
    if arg in ("启动", "加载"):
        if not privileged:
            send_group(gid, need_admin)
            return
        if comfy_up():
            send_group(gid, "ComfyUI 已经在线了，直接说画什么就行。")
            return
        send_group(gid, "正在启动本地 ComfyUI，好了我喊你。")

        def _boot():
            try:
                ok = comfy_gen.boot(c)
                _draw_state["up_t"] = 0.0
                send_group(gid, "ComfyUI 起好了，说「画：xxx」就行。" if ok
                           else "ComfyUI 起来了但接口没应答，看 comfyui_qqbot.log")
            except Exception as e:
                send_group(gid, "启动失败：%s" % _short_err(e))
        threading.Thread(target=_boot, daemon=True).start()
        return
    if arg in ("卸载", "清理"):
        if not privileged:
            send_group(gid, need_admin)
            return
        try:
            comfy_gen.unload(c)
            _draw_state["up_t"] = 0.0
            send_group(gid, "模型已从显存卸掉了。")
        except Exception as e:
            send_group(gid, "卸载失败：%s" % _short_err(e))
        return
    if arg == "重查":
        if not privileged:
            send_group(gid, need_admin)
            return
        book = _subject_book()
        if book is None:
            send_group(gid, "现在没开角色设定查证（config 里 comfyui.subject_lookup 是关的）。")
            return
        left = book.clear()
        send_group(gid, "查过的角色设定清了（剩 %d 条），下次画会重新联网查。" % left)
        return


def handle_engine_switch(gid, raw, privileged):
    """「comfyui 开/关」「mossapi 开/关」——**按群**开关某一条生图链路。

    主人 2026-10-24 定的模型：两条链路各自独立，**选了就开、没选就关**（白名单）。
    所以这两条口令只影响**当前这个群**，别的群不受牵连。
    """
    m = ENGINE_SWITCH_RE.match((raw or "").strip())
    if not m:
        return
    word = (m.group(1) or "").lower().replace(" ", "")
    arg = (m.group(2) or "").lower()
    eng = "moss" if word in ("mossapi", "moss", "mosi") else "comfyui"
    on = arg in ("开", "on")
    name = "云端 Moss" if eng == "moss" else "本地 ComfyUI"
    if not privileged:
        send_group(gid, "这个要管理员权限。")
        return
    if eng == "moss" and on and not moss_enabled():
        send_group(gid, "开不了云端 Moss：config.json 的 moss.api_key 是空的"
                        "（或 moss_gen 模块没加载）。")
        return
    draw_set_group_engine(gid, eng, on, src="qq")
    if on:
        extra = ""
        if eng == "comfyui" and not comfy_enabled():
            extra = "\n⚠️ 「画图 开」这个总开关现在是关的，得先发「画图 开」才真能画。"
        elif eng == "comfyui" and not comfy_up():
            extra = "\n（本地 ComfyUI 现在没在跑，管理员发「画图 启动」我拉起来）"
        send_group(gid, "本群已开启 %s%s\n发「画：描述」就能画。" % (name, extra))
    else:
        send_group(gid, "本群已关闭 %s。" % name)
    # 顺手报一下本群两条链路的现状，省得再问一次
    on_c, on_m = engine_on(gid, "comfyui"), engine_on(gid, "moss")
    cur = draw_engine(gid)
    send_group(gid, "本群现在：comfyui %s / mossapi %s → %s" % (
        "开" if on_c else "关", "开" if on_m else "关",
        ("走 " + ("云端 Moss" if cur == "moss" else "本地 ComfyUI")) if cur else "两条都关着，画不了"))


def handle_comfy_engine(gid, arg, privileged):
    """「画图 引擎 moss|comfyui」——切换生图链路。

    两条路的差别（主人 2026-10-24 定）：
      · comfyui：本地显卡跑，**必须**管理员先发「画图 开」；
      · moss：走 MOSI 官方云 API（**免费**），不占显存、不用开画图开关，直接能画。
    """
    if not privileged:
        send_group(gid, "这个要管理员权限。")
        return
    a = (arg or "").strip().lower()
    cur = draw_engine()
    if not a:
        send_group(gid, "生图引擎 = %s（comfyui=本地，要先「画图 开」；moss=云端 Moss，不用开）。"
                        "发「画图 引擎 moss」或「画图 引擎 comfyui」切换。" % cur)
        return
    if a in ("moss", "mosi", "云", "云端", "cloud"):
        if not moss_enabled():
            send_group(gid, "切不了：config.json 的 moss.api_key 是空的（或 moss_gen 模块没加载）。")
            return
        CFG.setdefault("comfyui", {})["engine"] = "moss"
        save_config()
        log.info("生图引擎 -> moss（%s）", "管理员口令")
        send_group(gid, "生图已切到云端 Moss：不用「画图 开」，直接「画：描述」就能画，约 15~30 秒一张。"
                        "（图生图/画同款还是要本地 ComfyUI）")
        return
    if a in ("comfyui", "comfy", "本地", "local", "本地流"):
        CFG.setdefault("comfyui", {})["engine"] = "comfyui"
        save_config()
        log.info("生图引擎 -> comfyui（管理员口令）")
        send_group(gid, "生图已切回本地 ComfyUI：得先「画图 开」（或管理员发「画图 启动」拉服务）。")
        return
    send_group(gid, "只认 comfyui / moss 两种，收到「%s」。" % a)


def handle_comfy_style(gid, arg, privileged):
    """「画图 档 krea2|Anima工作流」——换默认档。等价于「设置 画图档 krea2」。"""
    c = _comfy_cfg()
    if not arg:
        send_group(gid, "画图档 = %s（可选 %s）。发「画图 档 krea2」换，或「设置 画图档 Anima工作流」。" % (
            c.get("default_style"), "、".join(_style_names())))
        return
    _ok, msg = knob_set("画图档", arg, privileged=privileged, ctx={"gid": gid})
    send_group(gid, msg)
