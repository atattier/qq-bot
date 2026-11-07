# -*- coding: utf-8 -*-
"""「生成视频」口令链测试（2026-10-01）：解析、闸门、提示词替换点。save_config 打桩。"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import json
import bot

bot.save_config = lambda: True
SENT = []
bot.send_group = lambda gid, text: SENT.append((gid, text)) or True

bad = 0

def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("   [OK] " if ok else "   [失败] ") + name + ("" if ok else " | %s" % (why,)))

# 1) 口令解析
for raw, want_desc in (
        ("生成视频 一只白猫在雨中撑伞走过小巷", "一只白猫在雨中撑伞走过小巷"),
        ("做视频：夕阳下的富士山，樱花飘落", "夕阳下的富士山，樱花飘落"),
        ("视频生成 一段赛博朋克城市的夜景", "一段赛博朋克城市的夜景"),
        ("生成视频", ""),                      # 空描述也要命中（闸门回提示）
        ("参考视频生视频，主角替换为我刚发的图片角色", "主角替换为我刚发的图片角色"),
        ("参考图生视频 让她转身跳舞", "让她转身跳舞"),
        ("参考图片生视频：主角换成图里的女孩", "主角换成图里的女孩"),
        ("生视频 镜头1：桌上的茶壶喷出热气", "镜头1：桌上的茶壶喷出热气"),  # 少写「成」也要命中（2026-10-01 实测被画图「生」截胡）
        ("画一只猫", None),                    # 不能误吞画图口令
        ("帮我生成一段视频的剧本", None),      # 前缀不在句首不吞
        ("推荐几个视频生成工具", None)):
    hit, desc = bot.wants_video_gen(raw)
    if want_desc is None:
        chk("不误伤 %r" % raw, not hit)
    else:
        chk("命中 %r -> %r" % (raw[:12], want_desc[:10]), hit and desc == want_desc, (hit, desc))

# 1b) 素材记忆：登记 → 回捞 / 30 分钟过期 / 别人捞不到
seg_img = {"file": "abc.jpg", "url": "https://example.com/abc.jpg"}
seg_vid = {"file_id": "v123", "url": "https://example.com/v.mp4"}
bot._vg_remember_assets(123450001, 111, imgs=[seg_img])
bot._vg_remember_assets(123450001, 111, video=seg_vid)   # 图和视频都留着
mi, mv = bot._vg_recall_assets(123450001, 111)
chk("素材记忆：图和视频都记得", mi and mv and mi[0].get("file") == "abc.jpg", (mi, mv))
mi2, mv2 = bot._vg_recall_assets(123450001, 222)
chk("素材记忆：别人捞不到", not mi2 and not mv2, (mi2, mv2))
bot._vg_assets[(str(123450001), str(111))]["ts"] = 0.0    # 伪造成 1970 年
mi3, mv3 = bot._vg_recall_assets(123450001, 111)
chk("素材记忆：过期不回捞", not mi3 and not mv3, (mi3, mv3))

# 2) 闸门：关着 —— 2026-11-07 起 handle_video_gen 返回结构化事实（不再工具层说人话）
orig_cfg = dict(bot.CFG.get("comfyui") or {})
bot.CFG["comfyui"]["video_gen"] = {"enabled": False}
r = bot.handle_video_gen(123, "一只白猫在雨中撑伞走过小巷整条街", True)
chk("总开关关着会拒", isinstance(r, dict) and r.get("ok") is False
    and "关着" in str(r.get("error") or ""), r)

# 3) 闸门：描述太短
bot.CFG["comfyui"]["video_gen"] = {"enabled": True}
r = bot.handle_video_gen(123, "一只猫", True)
chk("描述太短会拒", isinstance(r, dict) and r.get("ok") is False
    and "太短" in str(r.get("error") or ""), r)

# 4) 闸门：非管理员（members_allowed 未开）
r = bot.handle_video_gen(123, "一只白猫在雨中撑伞走过小巷整条街", False)
chk("群友默认被拒", isinstance(r, dict) and r.get("ok") is False
    and "管理员" in str(r.get("error") or ""), r)

# 5) 排队路径（排上后 config 不落盘；队列要清干净别影响后续）
bot._vg_state["busy"] = False
bot._vg_state["last"] = 0.0
bot._vg_state["count"] = 0
r = bot.handle_video_gen(123, "一只白猫在雨中撑伞走过小巷整条街", True)
chk("管理员能排上", isinstance(r, dict) and r.get("ok") is True
    and r.get("queued") is True and not bot._vg_q.empty(), r)
while not bot._vg_q.empty():
    bot._vg_q.get_nowait()
    bot._vg_q.task_done()

# 6) 忙碌闸
bot._vg_state["busy"] = True
r = bot.handle_video_gen(123, "一只白猫在雨中撑伞走过小巷整条街", True)
chk("忙时拒第二次", isinstance(r, dict) and r.get("ok") is False
    and "生成" in str(r.get("error") or ""), r)
bot._vg_state["busy"] = False

# 7) 提示词/成片节点：**当前 config 生效的那份**工作流
#    （2026-10-08 起 = 「H3fast 一采.json」：只跑一采、latent 传递与二采已删）
from qbot import comfy_gen
_HAVE_VG_WF = True
try:
    comfy_gen.wf_load(bot._comfy_cfg(), bot._vg_cfg()["workflow"])
except Exception:
    _HAVE_VG_WF = False
    print("   (跳过) 本机没有视频工作流文件")
if _HAVE_VG_WF:
    CFG_C = bot._comfy_cfg()
    _cw = bot._vg_cfg()["workflow"]
    g, meta = comfy_gen.wf_to_api(comfy_gen.wf_load(CFG_C, _cw)[1])
    cand = [k for k, v in g.items() if v["class_type"] == "PrimitiveStringMultiline"]
    chk("转换图里找得到提示词节点", bool(cand), cand)
    pk = max(cand, key=lambda k: len(str(g[k]["inputs"].get("text")
                                          or g[k]["inputs"].get("value") or "")))
    chk("提示词节点是最长文本的那个 PrimitiveStringMultiline", bool(pk), pk)
    chk("meta.kind = video（认得出视频输出）", meta.get("kind") == "video", meta.get("kind"))
    # 一采版必须满足的三条（不然就退回二采/空壳提交了）
    _sam = [k for k, v in g.items() if v["class_type"] == "SamplerCustomAdvanced"]
    chk("只剩一个采样器（二采链已删）", len(_sam) == 1, _sam)
    _mc = [k for k, v in g.items() if "MotionContext" in v["class_type"]]
    chk("没有 MotionContext（latent 传递已删）", not _mc, _mc)
    _prev = [k for k, v in g.items() if v["class_type"] in ("PreviewImage", "SaveImage")]
    chk("没有 PreviewImage/SaveImage 抢输出", not _prev, _prev)
    # 一采必须吃完整 sigma（不是二采切出来的前 4 步）
    _sig = g.get("157", {}).get("inputs", {}).get("sigmas")
    chk("一采 sigmas 接的是 BasicScheduler（完整步数）",
        isinstance(_sig, list) and g.get(str(_sig[0]), {}).get("class_type") == "BasicScheduler", _sig)

# 7b) 成片帧率：VHS_VideoCombine.frame_rate / CreateVideo.fps 都要归一化成 24
#     帧数由 ComfyMathExpression 的 round(a*24) 算出（a=秒）；封装帧率写成 8 时
#     192 帧 = 24 秒（慢放 3 倍 + 后 16 秒静音），主人 2026-10-02 实锤
def _frs(gg):
    out = {}
    for k, v in gg.items():
        if v.get("class_type") == "VHS_VideoCombine":
            out[k] = ("frame_rate", v["inputs"].get("frame_rate"))
        elif v.get("class_type") == "CreateVideo":
            out[k] = ("fps", v["inputs"].get("fps"))
    return out
if _HAVE_VG_WF:
    _before = _frs(g)
    chk("转换图里有成片节点（VHS_VideoCombine 或 CreateVideo）", bool(_before), _before)
    bot._vg_force_fr24(g)
    _after = _frs(g)
    chk("归一化后全是 24fps（原始 %s）" % _before,
        bool(_after) and all(x[1] == 24 for x in _after.values()), _after)
# 两种来源都要按：根本没有该键 + 文件里带老值 8.0
_fake = {"299": {"class_type": "VHS_VideoCombine", "inputs": {}},
         "408": {"class_type": "VHS_VideoCombine", "inputs": {"frame_rate": 8.0}},
         "500": {"class_type": "CreateVideo", "inputs": {"fps": 8.0}},
         "9": {"class_type": "SaveImage", "inputs": {}}}
_fx = bot._vg_force_fr24(_fake)
chk("缺键补 24 / 老值 8.0 纠 24 / CreateVideo.fps 也纠 / 无关节点不动",
    _fake["299"]["inputs"]["frame_rate"] == 24 and _fake["408"]["inputs"]["frame_rate"] == 24
    and _fake["500"]["inputs"]["fps"] == 24
    and "frame_rate" not in _fake["9"]["inputs"]
    and dict(_fx) == {"299": None, "408": 8.0, "500": 8.0}, _fx)

# 7c) 视频长度由描述控制（2026-10-08 加）：写「N 秒」就改工作流里的秒数节点
chk("时长解析直接认「8秒」", bot._vg_secs_from_desc("一只猫在雨里跑 8 秒") == 8.0)
chk("时长解析认「6 seconds」", bot._vg_secs_from_desc("walk 6 seconds") == 6.0)
chk("时长解析认中文「七秒」", bot._vg_secs_from_desc("七秒的舞蹈") == 7.0)
chk("没写秒数就不改（返回 None）", bot._vg_secs_from_desc("让它动起来") is None)
if _HAVE_VG_WF:
    _g3 = comfy_gen.wf_to_api(comfy_gen.wf_load(CFG_C, _cw)[1])[0]
    _sec, _node = bot._vg_apply_duration(_g3, "镜头推进，12秒", hi=30.0)
    chk("写进工作流的秒数节点并真的改了值",
        _sec == 12.0 and _node and float(_g3[_node]["inputs"]["value"]) == 12.0, (_sec, _node))
    _g4 = comfy_gen.wf_to_api(comfy_gen.wf_load(CFG_C, _cw)[1])[0]
    chk("描述里没秒数就不动那个节点",
        bot._vg_apply_duration(_g4, "随便动一下", hi=30.0) == (None, None))

    # 7d) 画布比例 / 百万像素可调（2026-10-08 主人要求：0.6MP、默认 3:4）
    #     ⚠️ ResolutionSelector 的 aspect_ratio 是 COMBO，只认那 8 个名字，逐字相同
    chk("比例候选只有 ResolutionSelector 认的那 8 档",
        bot._vg_aspect_choices() == ["3:4", "9:16", "1:1", "4:3", "16:9", "2:3", "3:2", "21:9"],
        bot._vg_aspect_choices())
    chk("短名映射到节点 COMBO 全名", bot._vg_aspect_full("3:4") == "3:4 (Portrait Standard)")
    chk("默认比例是 3:4", bot.VIDEO_ASPECT_DEFAULT == "3:4")
    chk("默认百万像素是 0.6", float(bot._vg_cfg().get("megapixels")) == 0.6)
    chk("不认识的比例会被 check 拦下", bot._vg_aspect_check("6:19")[0] is False)
    chk("支持的比例 check 放行", bot._vg_aspect_check("16:9")[0] is True)
    chk("百万像素越界会被拦", bot._vg_mp_check("9")[0] is False and bot._vg_mp_check("0.6")[0] is True)
    _g5 = comfy_gen.wf_to_api(comfy_gen.wf_load(CFG_C, _cw)[1])[0]
    _asp, _full, _n = bot._vg_apply_aspect(_g5, "16:9", 0.8)
    _rsel = [v for v in _g5.values() if v.get("class_type") == "ResolutionSelector"]
    chk("按 config 覆盖 ResolutionSelector 真的写进去了",
        _n >= 1 and _rsel and _rsel[0]["inputs"].get("aspect_ratio") == "16:9 (Widescreen)"
        and float(_rsel[0]["inputs"].get("megapixels")) == 0.8, (_asp, _full, _n))
    _g6 = comfy_gen.wf_to_api(comfy_gen.wf_load(CFG_C, _cw)[1])[0]
    chk("比例传空串 = 不动工作流",
        bot._vg_apply_aspect(_g6, "") == (None, None, 0))

# 7e) 产物识别：**键名随节点走**（2026-10-08 主人「视频运行好了为啥不发出去」实锤）
#     旧 VHS_VideoCombine ⇒ gifs；新 SaveVideo ⇒ images。只认 gifs = 成功也误报「没有产出」。
_ENT_SAVEVIDEO = {"status": {"status_str": "success", "completed": True, "messages": []},
                  "outputs": {"92": {"images": [{"filename": "MiniMax_H3_00087_.mp4",
                                                "subfolder": "video", "type": "output"}],
                                     "animated": [True]}}}
_v = bot._vg_gifs_of(_ENT_SAVEVIDEO)
chk("SaveVideo 的产物（images 键）认得出",
    len(_v) == 1 and _v[0]["filename"] == "MiniMax_H3_00087_.mp4"
    and _v[0]["subfolder"] == "video", _v)
_ENT_VHS = {"status": {"status_str": "success"},
            "outputs": {"299": {"gifs": [{"filename": "old.mp4", "subfolder": "",
                                          "format": "video/h264-mp4"}]}}}
chk("旧 VHS 的产物（gifs 键）照样认得出", len(bot._vg_gifs_of(_ENT_VHS)) == 1)
# images 里全是 png（只有 SaveImage/PreviewImage）⇒ 不能当视频产物
_ENT_PNG = {"status": {"status_str": "success"},
            "outputs": {"9": {"images": [{"filename": "ComfyUI_00155.png", "subfolder": ""}]}}}
try:
    bot._vg_gifs_of(_ENT_PNG)
    chk("只有 png 时要报没产出", False)
except comfy_gen.ComfyError:
    chk("只有 png 时要报没产出", True)
# 真报错时要把节点号带出来
try:
    bot._vg_gifs_of({"status": {"status_str": "error",
                                "messages": [["execution_error",
                                              {"node_id": 92, "node_type": "SaveVideo",
                                               "exception_type": "RuntimeError"}]]},
                     "outputs": {}})
    chk("出错时带节点号", False)
except comfy_gen.ComfyError as _e:
    chk("出错时带节点号", "92" in str(_e) and "SaveVideo" in str(_e), str(_e))

# 8) 网页旋钮
items = {i["key"] for grp in bot.knob_items() for i in (grp.get("items") if isinstance(grp, dict) else [])} \
    if not isinstance(bot.knob_items(), list) else {i["key"] for i in bot.knob_items()}
need = {"comfyui.video_gen.enabled", "comfyui.video_gen.members_allowed",
        "comfyui.video_gen.cooldown_sec", "comfyui.video_gen.max_per_hour",
        "comfyui.video_gen.timeout_sec", "comfyui.video_gen.workflow",
        "comfyui.video_gen.max_seconds", "comfyui.video_gen.aspect",
        "comfyui.video_gen.megapixels", "comfyui.require_addressing"}
chk("视频生成旋钮都进了 /admin 数据", need <= items, need - items)

# 8b) 群里没被点名就不跑生图/生视频（2026-10-08 主人要求）
#     详细行为在 tests/_tat.py；这里只保它别被谁顺手删掉
chk("「群里要@」闸门在这里", callable(getattr(bot, "_comfy_at_ok", None)))
chk("「群里要@」默认开", bot._comfy_at_required() is True)
chk("_comfy_at_ok 群里没@ 拦住 / @了放行",
    bot._comfy_at_ok(1, True, False, "t") is False and bot._comfy_at_ok(1, True, True, "t") is True)

# 9) 忙闸门：worker 跑完「反推」（caption）也必须松开 busy
#    不修则反推一次就把开关焊死、之后所有视频单都被拒，直到 bot 重启
#    （2026-10-02 主人「为啥不生成」实锤：17:55 反推成功 → 17:55 视频单被拒）
import threading
import time as _time
_called = []
bot._do_caption_gen = lambda job: _called.append(job)
bot._vg_state["busy"] = True              # 故意先置忙，模拟 worker 刚取到任务
threading.Thread(target=bot._vg_worker, daemon=True).start()
bot._vg_q.put({"kind": "caption", "gid": -1})
bot._vg_q.join()
_time.sleep(0.3)
chk("反推任务跑完 busy 复位（不再焊死）", bot._vg_state["busy"] is False, bot._vg_state["busy"])
chk("caption 任务确实被 worker 取走执行了", bool(_called), _called)

bot.CFG["comfyui"] = orig_cfg
print("RESULT:", "PASS" if bad == 0 else "FAIL")
sys.exit(1 if bad else 0)
