"""统一回归入口（2026-10-10 整理时加）。

以前每个测试靠人记「改了 X 要跑 Y」，写在 HANDOVER 里，容易漏。现在一条命令跑完。

用法：
    python tests/run_tests.py            # 跑 SAFE 集（默认，日常必跑）
    python tests/run_tests.py --all      # 跑 SAFE + MANUAL 全说明
    python tests/run_tests.py --only _tdraw _tset
    python tests/run_tests.py --list

约定：
- SAFE 集 = 全打桩、不联网、不起常驻进程、不碰真 config.json，可以随便跑。
- MANUAL 集 = 会真花钱（真 LLM）、真启进程或依赖本机环境，只在改动相关区域时手动跑。
"""

import subprocess
import sys
import time
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# (测试名, 覆盖区域, 说明)
SAFE = [
    ("_tdraw.py",     "现画链路",     "改动 ComfyUI 出图必跑，21 用例全打桩"),
    ("_tset.py",      "统一控制面",   "旋钮注册表 61 项，会校验真 config.json 字节未变"),
    ("_tadminjs.py",  "后台页面JS",   "admin_html 的 <script> 必过 node --check（10-29 语法错炸全页的守卫）"),
    ("_tvlimgcompress.py", "送图压缩", "云端/本地双预算 + 必须转 JPEG（10-29 线上识图全线 413 的守卫）"),
    ("_tsee_follow.py", "识图跟随聊天", "识图共用大脑模型 + 一图直达准入闸（10-30 主人要的机制）"),
    ("_tomni.py", "总控大脑", "一个大脑带全套工具自己分配；按身份裁工具面；不支持 tools 必回落（10-30 主人拍板）"),
    ("_tnotify.py",   "notify 安全", "/notify token 鉴权 + 群白名单 + 非法 gid 拒绝；发送全打桩（2026-11-04 R3）"),
    ("_thttp.py",     "HTTP 层",      "本地 18088 端到端，含 401 鉴权"),
    ("_tconsole.py",  "新控制台",     "/console 路由/token 门/静态资源/路径安全 + 旧 /admin 不回归（2026-11 阶段 1）"),
    ("_troute.py",    "意图路由评测", "15 例真实表达桩执行：分支命中/参数正确/否定撤回零容忍（阶段1C 评测集）"),
    ("_tbudget.py",   "工具边界预算", "预算硬闸/同轮去重/画图单发/失败停止/多步骤例外（阶段2 离线单测）"),
    ("_ttoolauth.py", "工具执行授权", "R2：非主人工具面下模型硬写 qq_send_group/remember 必须被执行层拒绝，主人路径不受影响，全打桩"),
    ("_treporttoken.py", "上报 token 认证", "R4：上报 token 独立于 API token·多上报端·未配置兼容告警·缺失/畸形 Authorization 拒绝·API 仍用 config.token"),
    ("_tquote.py",    "全局引用读取", "reply 三形状/正文上限/引用图进大脑（2026-11-03 主人实测的引用丢失）"),
    ("_tban.py",      "群违禁词入口", "群里命中要撤回+警告、私聊不掺和（U12a 抽取曾漏传 uid → NameError 的回归）"),
    ("_tu12e.py",     "HTTP 路由表",  "U12e：GET31/POST24 保序前缀逐条对账、顺序陷阱、4 条 do_POST 源码级安全守卫、46 个 action_* 签名不变"),
    ("_tu12b.py",     "现画域抽取",  "U12b：桥接宿主全局符号全等对账、RETIRED 登记有意删除函数、测试桩实时可见、draw_time.json 测试态隔离（字节/AST 基线锁已撤）"),
    ("_tslang.py",    "黑话四件套",   "证据链带真实mid/阈值推断/未确认不当事实/MANUAL不可覆盖/无关不注入/关闭降级，全打桩（人味阶段A2）"),
    ("_texpr.py",     "表达学习闭环", "溯源（AI指不存在的消息整条丢弃）/checked_only/管理员不被覆盖/群隔离/黑名单过滤/回滚，全打桩（人味阶段A3）"),
    ("_ta.py",        "本地 API",     "/api/* + MCP 接口"),
    ("_tvg.py",       "视频生成",     "H3 链路"),
    ("_tgroup.py",    "群消息处理",   "路由 / 点名 / 权限"),
    ("_trandom.py",   "随机抽图",     "-----"),
    ("_tscramble.py", "图片混淆",     "含端到端 C 段"),
    ("_tjm.py",       "JM 漫画",      "内部会自动临时开 allow_group"),
    ("_tknob.py",     "旋钮查重",     "防重名打错靶"),
    ("_tpersona.py",  "人格切换",     "-----"),
    ("_tsim.py",      "仿真群友",     "-----"),
    ("_tmem.py",      "长期记忆模块", "SQLite 群友画像：读写/关键词检索/遗忘，跑在临时库"),
    ("_tmemread.py", "读记忆边界", "R1：action_memory_read/GET /api/memory 会话必填；群/私聊哨兵隔离，缺/非法 group 拒绝全局读取，临时库"),
    ("_tmemscope.py", "记忆会话隔离", "R1 strict：group A/B/private A/B 哨兵互不可见；_memory_ctx_blocks/agent/sim_join/ai_reply/profile/render/recall 全链覆盖，临时库"),
    ("_tmemfusion.py", "记忆检索融合", "阶段D实验：默认线性不变·RRF 三信号融合·分数归一化·P75 动态阈值≥4条·切回线性恢复，全离线"),
    ("_tmemcard.py",  "衰减记忆卡",   "SurfCore 移植：脱敏/评分/衰减/复习/压缩/全量归档/会话隔离/开场话头，临时目录"),
    ("_tmemapi.py",   "长期记忆接口", "MCP stdio 冒烟：工具注册 + 容错（dummy token 必被拒）"),
    ("_tsee.py",      "聊天识图",     "通用看图（非人物也看得懂）+ ROLE 标记 + 认人闸门，全打桩"),
    ("_thuman.py",    "拟人化/闸门",  "分条·打字时长·限速·熔断·合批·人设体检，全离线"),
    ("_tskills.py",   "技能包加载",   "skill.json 扫描/开关/参数/提示词预算/坏包容错，全离线"),
    ("_tpetpet.py",   "捏脸表情包",   "13 个模板渲染/口令识别/冷却额度/关技能降级，头像用假图不联网"),
    ("_tmeme.py",     "本地meme服务客户端", "阶段3-C：口令识别/地址防篡改/错误码分流/结果校验/临时清理，全桩离线"),
    ("_tstatuscard.py", "机器状态卡", "绘制(有卡/无卡/空数据)/口令识别/冷却/开关，全离线"),
    ("_timgb64.py",   "图片/视频发送兜底", "NapCat 在容器里读不到本机路径 → 自动改 base64 重发（视频单独放宽上限），全打桩"),
    ("_tlinkprev.py", "链接速览", "URL 抽取/公网过滤/gzip/GitHub 三档判定/缓存/渲染，全打桩不联网"),
    ("_tlong.py",     "长文投递",     "分段不截断/长文装箱/token 预算/提示词允许长文，全离线"),
    ("_tcover.py",    "语音/翻唱",    "点播路由/网易云解析/试听片段拒绝，全离线"),
    ("_tpriv.py",     "私聊发送",     "group_hint 贯通 / 群消息丢弃 hint / 非好友私聊链路，全打桩"),
    ("_tident.py",    "主人身份段",   "私聊≠主人（按 uid 判）·群聊标明发信人·识图侧同口径，全离线"),
    ("_tatqq.py",     "真@与号码禁令", "@昵称转真段(带装饰名片也能命中)·裸号码必抹不误伤链接·陌生人叫哥哥硬闸，全离线"),
    ("_tatident.py",  "出口真@与分人", "带引用/段列表也转真@(防绕过send_group)·上下文与HISTORY每行带QQ·合批逐人标注，全离线"),
    ("_topener.py",   "开场去重",   "同一件旧事不得连开多次场：开场成功后候选原文进 purpose_seen·第二次换下一条·用过不再硬凑，全离线（11-04 主人实测修复）"),
    ("_tsimquiet.py", "闭嘴的艺术", "表情包只读情绪不评画面(不许整条跳过)·单条不逐句接·激烈讨论不插嘴·攒整段才判，全离线"),
    ("_ttiming.py",   "时机层",     "概率门权重锚点·自校准只收紧不放宽·退避/积压绕过·WAIT状态机·总闸关=旧行为，全离线（人味阶段B）"),
    ("_tpurpose.py", "主动性层", "goal 证据/归属/隐私过滤·生命周期·多层去重·池容量/TTL/轮换·日上限/间隔/安静/恢复·owner三态·mood软权重·timing退避·只读模式·关闸=旧行为，全离线（人味阶段C）"),
    ("_tstateiso.py", "测试态隔离", "mood/slang/expression/timing/REV_DIR/scramble/wire备份 全部临时目录 + 真写一次后生产 state hash 不变（2026-11-04 审计小修）"),
    ("_tisolation.py", "隔离注册守卫", "U4 新增：凡模块级落盘文件必须登记在 _tisolation._STATE_FILES 且测试态全落临时目录——新模块忘登记 = SAFE 红（2026-11-05 审计 P1-11 的系统性修复）"),
    ("_ttestmute.py", "测试静音", "测试/演练的发言绝不进真群：唯一出口静音闸·/onebot 拒收来路不明注入，全离线"),
    ("_tentry.py", "生产入口回归", "阶段3验收①：经 handle_group/private_message 全链 11 例（引用JSON直发/改图分流/指定引擎/降级/工具面裁剪/引用围栏）"),
    ("_tengine.py", "引擎决策矩阵", "阶段3验收②：17 格矩阵（draw_engine/用户指定优先/raw_prompt→moss/handle_i2i分流/draw_like_this 动态分流），全桩"),
    ("_tqr.py",       "二维码与保活", "show_qr 导码 / keeper 等扫码判据，容器不在也有降级"),
    # ---- 2026-11-05 U3 纳管：原孤儿测试（审计 P1-14/P1-15）----
    ("_trevfallback.py", "反推 VLM 接力", "DeepSeek→Ollama 接力离线桩，含「429-as-text 假成功」真修过的用例（原孤儿，注册找回）"),
    ("_tat.py", "生图@闸门", "「群里没被@不跑生图/生视频」四出口闸门 27 例，驱动真 _handle_message（原孤儿，注册找回）"),
    ("test_reflect.py", "反思闭环", "qbot_reflect 28 项：人格隔离/事实校验/幂等/重启恢复，全临时库（原在 tools/ 游离，U3 迁入）"),
    ("test_skill_edit.py", "技能编辑链路", "160 项：技能加载/编辑/市场装卸/插件卡形状，全沙盒（原在 tools/ 游离，U3 迁入）"),
    # ---- 2026-11-05 M0：Golden Set（ADR-001，独立基线）----
    ("golden/_tgolden.py", "M0 Golden Set", "240 条脱敏样本：schema/覆盖/脱敏/近重复校验 + 评测器 oracle/变异自测；不联网不发消息"),
    # ---- 2026-11-05 M1：Turn + 影子核心 ----
    ("_tshadow.py", "Turn + 影子核心", "Turn 契约/Policy/Core/队列/总闸/覆盖率/差异分类，全离线假模型；不发消息不执行工具"),
    ("_tshadow_entry.py", "影子入口接线", "真实 _handle_message：影子开/关旧路径发送逐字节一致、Turn 入队、发送观测与差异分类，全桩离线"),
    # ---- 2026-11-05 M2：Context Assembler ----
    ("_tcontext.py", "上下文组装", "18 段契约/来源优先级/预算丢段/关键段保留/媒体跨轮/会话与私聊隔离/逐段回退/legacy 回退，全离线"),
    # ---- 2026-11-05 M3：Tool Registry ----
    ("_tregistry.py", "工具注册表", "14+20 工具全覆盖/四档权限矩阵/越权拦截/ToolResult 超时幂等/MCP 生成等价/影子审计与 legacy 回退，全离线"),
    ("_tcleanup.py", "决策层收口", "删除式回归：无重复意图判定/无工具层固定发言/主脑链路与权限硬轨仍在，全离线"),
    ("_tbrain_takeover.py", "主脑接管验证", "架构方向：口令没接住的 5 类表达全落主脑 + 工具面 7 工具能触发执行 + 权限硬轨，全离线"),
    ("_tdraw_receipt.py", "画图终态回执", "排队≠出图；最近任务 queued/running/done/failed 与失败原因可被主脑查询，全离线"),
]

MANUAL = [
    ("_tinject.py", "真 LLM，会花钱", "阶段3验收③：提示注入回归 6 例（引用恶意指令不服从/金丝雀密钥不外发/对照例），改大脑 system 或引用链路时必跑"),
    ("_tphaseA.py", "真 LLM，会花钱（3~5 次调用）", "人味阶段A验收：真模型跑黑话阈值推断（炼丹→训练模型）+ 表达提取溯源（例句必须=原文），改 qbot_slang/qbot_expression 提示词时必跑"),
    ("_tphaseC.py", "真 LLM，会花钱（2 次调用）", "人味阶段C验收：记忆素材→goal→真模型输出（开口/[SILENT] 如实记录，发送打桩），改 qbot_purpose 提示词时必跑"),
    ("_tphaseB.py", "真 LLM，会花钱（2 次调用）", "人味阶段B验收：插话提示词的 [WAIT:秒] 选项（关闸无选项/开闸有选项/点名不许 WAIT），改 sim_join 提示词或时机层时必跑"),
    ("_tboundary.py", "真 LLM + 真网关，会花钱", "大脑边界感 18 例（阶段2）：改 brain_with_tools / 工具面 / system 提示词时必跑"),
    ("_tsub.py",  "真 LLM + 真联网，会花钱",        "验「先查设定再画」的真实效果时才跑"),
    ("_tboot.py", "真跑 start_all.bat",             "改动启动链路时才跑"),
    ("_tqq.py",   "一键启动 35 项，依赖 tests/config.json（本机缺失）", "改动一键启动时才跑"),
    ("_tmeme_entry.py", "真本地 meme 服务（127.0.0.1:2233）+ config.meme.enabled", "阶段3-D/4：五类模板经真实消息入口出图/会话隔离/引用图归属/临时清理，改 qbot_meme 接入时必跑"),
    ("_tmeme_reliability.py", "真本地 meme 服务 + 真生命周期控制（会 stop/restart 服务）", "阶段5：14 项可靠性场景（服务未启动/启动失败/重启/超时/错误码/超大损坏图/并发/清理/回归），改 meme 服务或接入时必跑"),
    ("_tkeeper_meme_guard.py", "真实验服务（独立端口2234）+ 真实启停，不碰生产Bot/2233", "方案A：keeper_guard Meme 守护 10 场景（健康复用/未启动拉起/意外恢复/启动失败隔离/端口占用不误杀/guard重启复用/无双实例/故障不重启Bot/keeper回归/退出不误杀）+6类日志事件"),
    # ---- 2026-11-05 U3 纳管：原孤儿测试（审计 P1-14）----
    ("_tstyle.py", "真 LLM，会花钱", "A1 反GPT腔前后对照：机械痕迹检测（客服腔/机械总结/模板分点），改人格提示词时跑（文档曾声称已注册，实际丢失，U3 找回）"),
    ("_trev_live.py", "真 ComfyUI + 真 VLM，会花钱", "真机反推链路（离线半由 _trevfallback 覆盖）"),
    ("_tmoss.py", "本机 ComfyUI 工作流文件", "宫格档端到端 67 项；硬依赖 <ComfyUI 目录> 工作流，缺失自动 SKIP（原孤儿，注册找回）"),
]

PY = sys.executable


def run_one(name, timeout=180, retry=True):
    """跑单个用例。失败自动重试一次 —— 因为这类用例里有「耗时预算」断言
    （_tscramble.py 的 `1536x1024 混淆 <4s`），机器忙时会真超，
    重跑一次能区分「偶发慢」和「真坏了」。
    """
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return False, 0.0, "文件不存在", []
    t0 = time.time()
    try:
        p = subprocess.run([PY, path], cwd=os.path.dirname(HERE),
                           env=dict(os.environ, QBOT_TEST="1"),
                           capture_output=True, text=True, timeout=timeout)
        ok = (p.returncode == 0)
        out = (p.stdout or "") + (p.stderr or "")
        lines = [x for x in out.strip().splitlines() if x.strip()]
        msg = lines[-1][:90] if lines else ("退出码 %d，无输出" % p.returncode)
        # 失败时把出错现场留下来，否则只有一行 PASS/FAIL，根本没法查
        detail = []
        if not ok:
            detail = [x for x in lines
                      if re.search(r"失败|FAIL|Traceback|Error|错误|期望|!=|Assertion", x)][-12:]
        if not ok and retry:
            spent = time.time() - t0
            print("           ^ 首跑失败（%s），机器忙的话可能是耗时预算超时，重试一次…" % msg[:40])
            ok2, sec2, msg2, d2 = run_one(name, timeout, retry=False)
            return ok2, spent + sec2, (msg2 + "  [重试后通过]" if ok2 else msg2), d2
        return ok, time.time() - t0, msg, detail
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, "超时 %ds" % timeout, []


def main():
    argv = sys.argv[1:]
    if "--list" in argv:
        print("SAFE（日常必跑，全打桩不联网）:")
        for n, area, note in SAFE:
            print("  %-16s %-12s %s" % (n, area, note))
        print("\nMANUAL（真服务 / 真花钱，按需手动跑）:")
        for n, why, note in MANUAL:
            print("  %-16s %s" % (n, why))
        return 0

    if "--only" in argv:
        i = argv.index("--only")
        names = [x if x.endswith(".py") else x + ".py" for x in argv[i + 1:]]
        todo = [(n, "指定", "") for n in names]
    else:
        todo = [(n, a, d) for n, a, d in SAFE]

    # --skip 用于把「依赖外部网络/已知会被上游限流」的用例单独拎出去跑：
    #   python tests/run_tests.py --skip _troute.py
    if "--skip" in argv:
        i = argv.index("--skip")
        skip = set(x if x.endswith(".py") else x + ".py" for x in argv[i + 1:])
        todo = [x for x in todo if x[0] not in skip]

    print("=" * 68)
    print("qq-bot 回归测试    用例 %d 个" % len(todo))
    print("=" * 68)

    results = []
    for name, area, note in todo:
        ok, sec, msg, detail = run_one(name)
        results.append((name, ok, sec))
        flag = "PASS" if ok else "FAIL"
        print("  [%s] %-16s %5.1fs  %s" % (flag, name, sec, msg))
        for d in detail:
            print("           | %s" % d[:96])

    bad = [r for r in results if not r[1]]
    print("-" * 68)
    if bad:
        print("失败 %d / %d：%s" % (len(bad), len(results), ", ".join(r[0] for r in bad)))
        print("注意：config.json 是启动时读一次的，改完配置要先重启 bot 再重跑。")
        return 1
    print("全部通过：%d / %d，耗时 %.1fs" % (len(results), len(results), sum(r[2] for r in results)))
    if "--all" in argv:
        print("\n以下需要真服务/真 LLM，本轮未跑，按需手动：")
        for n, why, note in MANUAL:
            print("  %-16s %s" % (n, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
