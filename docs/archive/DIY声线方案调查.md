# 自定义声线（DIY Voice）方案调查

> 日期：2026-10-09 ｜ 配套：`docs/发语音可行性调查.md`（发送通道已跑通）
> 问题：**主人要自己 DIY 声线，能做到吗？接什么软件？**

## 一、结论

**能做到，而且完全本地免费。** 推荐主接 **GPT-SoVITS（v2Pro 版本）** —— 中文社区最强、社区资源最多、自带 REST API 恰好匹配 bot 现有架构；只给 **5~15 秒**干净人声就能零样本克隆，想要更像可以再微调训练。

## 二、三条技术路线（先选路线，再选软件）

| 路线 | 做法 | 素材需求 | 效果 | 适合 |
|---|---|---|---|---|
| **① 零样本克隆** | 给一段参考音频，模型直接模仿 | **5~15 秒**干净人声 | 立即能用，相似度 80~95% | ★ 首选，当晚就能出效果 |
| **② 微调训练** | 拿素材训练专属模型 | **5~20 分钟**干净人声 | 最像、最稳（可到 95%+） | 想要"专属 IP 声线"，愿意花几十分钟训练 |
| **③ 变声（RVC）** | 先用 edge-tts 出声 → RVC 换成目标音色 | 需**目标音色的 RVC 模型**（可自训/找现成） | 像、快、省显存 | 想要**特定角色音**（动漫角色等），社区有现成模型 |

路线 ① 和 ② 是同一套软件（GPT-SoVITS）的两档用法；③ 是另一套软件（RVC），可叠加。

## 三、软件选型对比（2026 Q3 实测/公开榜单）

| 工具 | 显存 | 克隆素材 | 中文表现 | 许可 | 备注 |
|---|---|---|---|---|---|
| **GPT-SoVITS v2Pro** | **6~8G** | 5~15s | ★★★★★ | MIT | **本机首选**；自带 REST API；v2Pro 已修「混进参考原声」的老 bug |
| GPT-SoVITS v1/v2 | 4~6G | 5~15s | ★★★★ | MIT | 更省显存，音质基线 |
| GPT-SoVITS v3/v4 | 12G+ | 5~15s | ★★★★★ | MIT | 音质最高，**本机 8G 跑不动** |
| Fish Speech 1.6 | **3.2G** | 10s | ★★★★ | CC-BY-NC-SA | 最快（首帧 <500ms）、最省显存；**非商用许可** |
| IndexTTS-2 | 8G | 数秒 | ★★★★★ | 待核 | 情感/时长可控，首包 1.2s |
| CosyVoice 3 | 8G+ | 数秒 | ★★★★★ | Apache 2.0 | 0.5B、9 语言 + 18 中文方言 |
| Zonos2 8B | 16G+ | 10s | 高 | Apache 2.0 | 开源最强，**本机跑不动** |
| RVC（变声） | 1~2G | 需目标模型 | — | MIT | 叠加在 TTS 后面用 |
| ElevenLabs | 云端 | 30s | 良好 | 商用 | 订阅付费、国内直连不稳，**不推荐** |

## 三·补、「用 ComfyUI 出声音」实测结论（2026-10-09，查 `/object_info`）

**能出声音，但本机 ComfyUI 里能出语音的节点全是云 API，本地免费的一个都没有。**

- 本机 ComfyUI 共 **169 个音频相关节点**、其中 **286 个 `partner/` 云节点**。能 TTS / 声音克隆的：
  | 节点 | 归属 | 性质 |
  |---|---|---|
  | `FishAudioTextToSpeech` / `FishAudioInstantVoiceClone` | `partner/audio/Fish Audio` | ComfyUI 官方合作云节点，**要 key / 付费 credits** |
  | `ElevenLabsTextToSpeech` / `ElevenLabsInstantVoiceClone` | `partner/audio/ElevenLabs` | 同上，**付费** |
  | `RH_AlibabaQwen3TtsFlash` / `RH_BytedanceDoubaoSeedTts20` / `RH_MinimaxVoiceDesign` / `RH_RhartAudioTextToAudioVoiceClone` 等 | `RunningHub/*` | **走主人的 RunningHub 账号**，⚠️ 非免费时段 **3.75 元/次** |
- **本地（非 partner）音频节点**：全是「音频处理（音量/拼接/均衡）+ 音乐生成（ACE-Step / YuE2）+ 视频配音（LTXV audio / MiniMax H3 Speech）」—— **没有一个是文本转语音的 TTS**。
- 本地保存音频没问题：`SaveAudio` / `SaveAudioMP3` / `SaveAudioOpus` / `VRGDG_SaveAudio` 都在。

**结论**：
1. ⚠️ **不能用 RunningHub 云节点跑 bot 语音** —— 语音是高频功能，非免费时段每次 3.75 元，会烧钱（主人既定铁律：禁止花充值余额）。
2. 想让「ComfyUI 出声音」且**本地免费**，必须自己装第三方节点包，例如 **`ComfyUI-Fun-CosyVoice3`**（阿里 CosyVoice 3.0，0.5B，**4G 显存**、3~10 秒参考音频零样本克隆、9 语言 + 18 方言、Apache 2.0）—— 复用现有 ComfyUI 环境（py3.13 + torch 已就绪），装插件即可，bot 用现有 `comfy_gen.py` 的 HTTP 提交能力就能拿音频。
3. 但走 ComfyUI 有代价：**与出图/出视频抢显存** + 每次跑工作流要提交/加载（比独立服务慢）。

### 速度对比（含实测）

| 方式 | 延迟 | 成本 | 显存 | 备注 |
|---|---|---|---|---|
| **edge-tts**（已装） | **3.3s**（实测：5.2s 音频） | 免费无限 | **0** | ★ 最适合「偶尔发语音」 |
| ComfyUI + RH 云节点 | 5~30s（看平台排队） | **非免费时段 3.75 元/次** | 0 | ❌ 高频场景禁用 |
| ComfyUI + 本地 CosyVoice3 节点 | 冷启 15~30s / 热 5~10s | 免费 | 4G | 抢显存 |
| GPT-SoVITS 独立服务 | 3~8s（首包 1~3s） | 免费 | 6~8G | 中文最强，可训练专属模型 |
| Fish Audio / ElevenLabs 云节点 | 2~6s | 付费 | 0 | 要 key |

> ⚠️ 除 edge-tts 的 3.3s 是本机实测外，其余为公开资料值/估算，装好后需实测确认。

## 四、本机资源核算（2026-10-09 实测）

| 项 | 现状 | 结论 |
|---|---|---|
| GPU | RTX 4060 Laptop **8188 MiB**，当前已用 2317 MiB | v2Pro（6~8G）**能跑**，但与 ComfyUI 出图/出视频**会抢显存** |
| 磁盘 | E 盘 954G，**剩 60G**（94%）；C 盘剩 80G | GPT-SoVITS 整合包约 10~20G，**装得下但要挑盘** |
| Python 工具链 | **`uv` 已装**（`~/.local/bin/uv`）；ComfyUI 自带 py3.13.11 | 可用 uv 建独立 venv，干净隔离 |
| 已有 TTS 工具 | **无**（本机没有任何语音克隆工具） | 需要新装 |
| ComfyUI 节点 | 60 个，**无 TTS 节点**（唯一音频包是 H3 视频的 `minimax-h3-audio-T8`） | 走独立服务比塞进 ComfyUI 更合适 |
| edge-tts | 已装在 `<工具安装目录>/Python3.14.7` | 留作快档兜底 |

> ⚠️ **显存是唯一硬约束**：ComfyUI 出图时会占 6~7G。GPT-SoVITS 与它**不能同时满载**。对策：TTS 服务按需启停（`api_v2.py` 支持 `/control` restart/exit），或退到 v2 档 / Fish Speech（3.2G）。

## 五、bot 集成架构（双引擎）

```
文字 ─┬─ edge-tts（默认档：免显存、3s 出音）──────────┐
      └─ GPT-SoVITS api_v2（127.0.0.1:9880 /tts）─────┴─▶ mp3/wav ─▶ NapCat record 段 ─▶ QQ 语音条
```

- **接口形态完美匹配**：GPT-SoVITS 自带 `api_v2.py`，起服务后 `GET/POST /tts` 传 `text / text_lang / ref_audio_path / prompt_text / prompt_lang` 就能拿音频 —— bot 本来就是「调本地 HTTP 服务」的架构（ComfyUI/Ollama 同理），改动量小。
  ```bash
  python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
  ```
- **建议做成可选引擎**：`config.voice.engine = edge | sovits`，edge 为默认（快、零显存），要专属声线时切 sovits；断服务时自动回落 edge。

## 六、素材怎么准备

1. **自己录**：安静环境、单人、无 BGM，读 1~2 分钟文本 → 切 5~15 秒最干净的几段。
2. **从现成音频取**：动漫/视频里的角色音 → 用 **UVR5**（GPT-SoVITS 自带 `tools/uvr5`）分离人声再切片。
3. 质量控制：无人声重叠、无混响、无背景音乐；太短学不像、太长反而稀释。

## 七、部署方式（两条路）

- **A. 一键整合包（推荐，省事）**：中文社区有成熟 Windows 整合包（解压即用，含预训练模型 + WebUI + 环境），适合先跑起来玩。
- **B. 源码 + `uv`（干净可控）**：`git clone` 官方 `RVC-Boss/GPT-SoVITS` → `uv` 建 venv 装依赖 → 从 hf-mirror/ModelScope 拉预训练模型（国内直连走镜像）。适合要长期挂服务。

## 八、风险与限制

| 项 | 说明 |
|---|---|
| 显存冲突 | 与 ComfyUI 互斥满载；需按需启停或选小模型 |
| 速度 | 本地克隆推理比 edge-tts 慢（首包 1~3s，整句数秒）；群聊高频回复建议仍走 edge |
| 磁盘 | 整合包 10~20G；E 盘仅剩 60G，注意别再塞满 |
| 版权/合规 | 克隆**他人声音**（尤其明星/UP 主/角色配音）用于公开发布有侵权与平台风控风险 —— 群里玩可以，别外发商用 |
| 许可 | Fish Speech 是 CC-BY-NC-SA（非商用）；GPT-SoVITS 是 MIT（宽松） |

## 九·补、「bot 偶尔把消息也发一份语音」的实现设计

需求：bot 平时发文字，**偶尔**同一条内容再补发一条语音。建议设计（尚未实现）：

| 环节 | 设计 |
|---|---|
| 触发 | `config.voice.prob`（如 0.1~0.2）概率触发；可加条件（仅被@/仅私聊/仅特定人设场景） |
| 引擎 | `config.voice.engine = edge \| sovits \| comfy`，**默认 edge**（免费、3.3s、零显存）；服务不可用自动回落 edge |
| 长度闸 | 文本 > `max_chars`（如 60 字）直接跳过语音（QQ 语音条 ≤60s，避免又长又慢）；先按 `est_sec` 估时长 |
| 频率闸 | `cooldown_sec` + `max_per_hour`（照抄现画/视频那套；防刷屏、防风控） |
| 发送 | 新增 `send_voice(cid, path, group_hint)`，复用现有 `_api_send()`（群/私聊自动分发 + 私聊假失败复核），段 = `{"type":"record","data":{"file":<绝对路径>}}` |
| 落盘与清理 | 音频落 `state/tts/`，发后删（对齐现画 `delete_after_send`） |
| 线程 | 合成 3~8s，放**串行 worker**（照抄现画 `_draw_q`），不阻塞消息处理 |
| 配置面 | `/admin` 新增「语音」区（或并入「对话」区），`KNOBS` 注册；权限默认 admin 改、热生效 |
| 审计 | 合成**前**对文本跑 `audit_ok()`（现有审计只覆盖文本段，语音是二进制） |

**推荐落地顺序**：先做 **edge-tts 版**（今晚可用、零成本、零显存），把「偶尔发语音」跑起来；等声线定了再换引擎（改一个配置项的事）。

## 九、待主人拍板

1. **路线**：① 零样本克隆（5~15s，马上能用）② 微调训练（更像，要几十分钟训练）③ RVC 变声（要特定角色音）。
2. **声线来源**：自己录？还是从某段现成音频里取？（决定素材准备方式）
3. **部署**：一键整合包 vs `uv` 源码部署；装 E 盘还是 C 盘。
4. **是否双引擎**：默认 edge-tts 快回 + 需要时切 GPT-SoVITS 专属声线。

---

## 十、部署实录（2026-10-09，主人拍板「上 GPT-SoVITS」，已跑通）

**结论：可用。** 冷启动约 77s（含 CUDA 图预热），**热态 1.4~2.3s** 出一条，显存 **约 2.26GB**。

### 实测数据（本机 RTX 4060 Laptop 8GB）

| 项 | 实测 |
|---|---|
| 服务启动到就绪 | ~20~50s（加载 s1v3 / s2Gv2Pro / BERT / HuBERT / SV） |
| 首次合成（冷） | **77.4s** → 4.28s 音频 |
| 第二次（热） | **1.76s** → 2.06s 音频 |
| 第三次（热） | **2.25s** → 3.16s 音频 |
| bot 侧 `tts.py` 直连 | **1.38s** |
| 显存占用 | **2263 MiB**（空闲时全卡 373 MiB ⇒ TTS 约 1.9GB） |
| 端到端发 QQ 语音 | `status:ok / retcode:0 / message_id:2101262153`，且在好友消息历史里确认到该语音段 |

### 落地位置

`<工作目录>\GPT-SoVITS`（独立 venv，Python 3.11.16 + torch 2.9.1+cu128，CUDA 12.8 可用）

**启动**：双击 `start_tts.bat`（监听 `127.0.0.1:9880`）；关窗口即停。
**不开服务**时 bot **自动回落 edge-tts**（零显存），不会没声音。

### 踩到并修掉的坑（按重要性）

| # | 坑 | 处理 |
|---|---|---|
| 1 | `torch` 从 `download.pytorch.org` 装卡住（uv 走镜像只有 256KB/s） | 改 `curl -x http://127.0.0.1:7890` 直下 wheel（**11.7 MB/s**），再本地 `uv pip install <whl>` |
| 2 | `requirements.txt` 首行 `--no-binary=opencc` 强制源码编译 | 单列 `requirements.win.txt`，去掉该行，改用 opencc 轮子 |
| 3 | 🔴 **本机「安全删除」拦截**：uv 构建 sdist 时清理临时目录会失败（>50 文件需确认 / SHFileOperationW 0x2） | 凡是**无轮子且非必需**的源码包一律绕开（见下表） |
| 4 | `jieba_fast` 无 cp311 轮子、且 `chinese2.py` **直接 import 无回退** | `_fix_jieba_fast.py` 造纯 Python 转发垫片（`jieba_fast` → `jieba`，API 兼容） |
| 5 | `jieba` 本身也只有 sdist | `_fix_jieba.py` **手动解包**落盘 + 补最小 dist-info（纯 Python，无需构建） |
| 6 | `distance`（g2p_en 依赖）构建失败；但实测 **g2p_en 根本不调用它** | `_fix_distance.py` 纯 Python 实现；`g2p-en` 用 `--no-deps` 装，另补 `inflect` |
| 7 | 🔴 **缺 SV 模型**，服务启动即崩 | 补下 `pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt`（102.5MB）——`_dl_models.py` 清单已加 |
| 8 | 🔴 **torchaudio 2.9 把所有后端都收敛到 torchcodec**，本机 DLL 加载失败（WinError 127），装 0.8.1/0.9.0/0.17 全挂 | 给 `TTS.py` 加 `_load_audio_sf()`（soundfile 直读），替换 2 处 `torchaudio.load()`；原文件备份 `TTS.py.orig-20261009` |
| 9 | `resampy` 缺失（librosa 重采样） | `uv pip install resampy` |
| 10 | 参考音频文本没记录 ⇒ 零样本克隆 `prompt_text` 无从填写 | `_mkref.py` 用**已知文本**重生成 `refs/yuki_default.wav`（7.66s / 32kHz 单声道），文本同步写进 `refs/yuki_default.txt` 与 `config.voice.sovits.prompt_text` |

### 有意未装的三个包（及代价）

| 包 | 原因 | 代价 |
|---|---|---|
| `pyopenjtalk` | 只被 `text/japanese.py` 用（日语 G2P），仅源码包、需 cmake+MSVC | **日语合成不可用**（中文不受影响） |
| `funasr` | 只被 `tools/asr/funasr_asr.py` 用（训练前标注），拉 omegaconf→antlr4 触发删除拦截 | **ASR 自动标注不可用**（推理不受影响） |
| `jieba_fast`（真版） | 无轮子 | 用纯 Python 垫片，**分词略慢**，短句可忽略 |

### ⚠️ 显存的取舍（要主人定）

TTS 常驻 **约 1.9~2.3GB 显存**，而本机 ComfyUI 本来就靠 mmap 超分（模型 17.77GB ≫ 8.59GB）。
**默认没有把它写进 `start_all.bat`** —— 想用克隆声线就另开 `start_tts.bat`；不占显存时 bot 走 edge-tts 兜底。
若要「开机自动起」，需主人确认可以长期分掉这块显存。

### 换声线（DIY）

1. 录/剪一段 **3~10s 干净人声**（无背景音乐、无人声重叠），越干净越像。
2. `ffmpeg -i 原音频 -ar 32000 -ac 1 -c:a pcm_s16le refs/yuki_default.wav`
3. 把这段音频的**逐字文本**写进 `config.json → voice.sovits.prompt_text`（必须逐字一致，否则音色/韵律会飘）。
4. 重启 bot（config 只落盘不行，**必须重启**）。

### 关键脚本

| 文件 | 作用 |
|---|---|
| `start_tts.bat` | 起服务 |
| `_mkref.py` | 重新生成参考音频（已知文本） |
| `_tts_probe.py "文本"` | 探活 + 合成一条并报耗时/时长 |
| `_send_voice_e2e.py` | 端到端发一条 QQ 语音到主人私聊 |
| `_postinstall.py` | 解压 nltk_data / open_jtalk 词典 + 26 项导入自检 |
| `_fix_jieba.py` / `_fix_jieba_fast.py` / `_fix_distance.py` | 三个 Windows 适配垫片 |
| `_dl_models.py` | 预训练模型下载（含后补的 SV 模型） |
