# qq-bot

一个跑在 Windows 上的 QQ 群机器人，基于 [NapCat](https://github.com/NapNeko/NapCatQQ) /
OneBot 11。能聊天、画图、图生图与提示词反推、生成视频、合成语音、解析视频链接，
并带一个网页控制台和一套统一的配置面板。

> 这是一个自用机器人的公开快照：功能偏向作者的群聊场景，配置项多、默认值保守。
> 代码里的中文注释和内部文档保留了原样，方便对照实现细节。

## 功能

- **聊天**：多模型 provider 轮换（OpenAI 兼容接口 / 本地 Ollama），群聊/私聊、@ 与引用、
  上下文与长期记忆、人格文件、按群开关。
- **画图**：ComfyUI 工作流（文生图、图生图、风格/尺寸档位）、异步排队与终态回执；
  可选云端 Moss 布局。
- **反推与同款**：识图反推提示词（视觉模型或本地模型），再按提示词重绘。
- **视频**：图生视频/文生视频任务提交与轮询（可选）。
- **语音**：GPT-SoVITS 克隆声线，edge-tts 兜底；点播、翻唱（可选）。
- **其它**：B 站/抖音链接解析、JM 漫画、图片混淆、表情包、投票/接龙等群工具。
- **控制面**：`/admin` 网页控制台、`/api/*` 本地 HTTP 接口、旋钮式配置注册表、
  stdio MCP server（把 bot 能力暴露给外部 agent）。

## 环境要求

| 组件 | 说明 |
|---|---|
| Windows 10/11 | 启动脚本、保活与 NapCat 容器编排都按 Windows 编写 |
| Python 3.13+ | 主进程只依赖 Pillow + numpy；其余能力按需装可选依赖 |
| NapCat | QQ 协议端，推荐 Docker 容器版（见 `docker-napcat/`） |
| ComfyUI | 画图/视频，可选；在 `config.json` 的 `comfyui` 段配置地址与工作流 |
| Ollama | 本地视觉/文本模型，可选 |
| GPT-SoVITS | 语音克隆，可选；默认找 `~/GPT-SoVITS`，可用 `QBOT_SOVITS_HOME` 覆盖 |

## 快速开始

```bash
git clone https://github.com/atattier/qq-bot.git
cd qq-bot
pip install -r requirements.txt          # 核心依赖
pip install -r requirements-extra.txt    # 可选：语音 / 视频解析 / JM
```

1. **准备配置**：复制模板并填入自己的值。

   ```bat
   copy config.example.json config.json
   ```

   至少需要改：`napcat_http`（NapCat 的 OneBot 地址）、`token`（NapCat 上报 token）、
   `bridge_token`（bot 自己的 HTTP 接口口令）、`groups`（允许的群）、
   `ai.providers.*.key`。`config.json` 已被 `.gitignore` 忽略，不会入库。

2. **起 NapCat**（Docker 容器版）：

   ```bat
   docker_go.bat
   ```

   首次运行按提示在 `docker-napcat/.env` 填 `NAPCAT_ACCOUNT`，并用
   `看二维码.cmd` 扫码登录。容器相关说明见 `docker-napcat/README.md`。

3. **起 bot**：

   ```bat
   start_qq.bat
   ```

   一键脚本会拉起 NapCat、等 bot 就绪、打开控制台，然后转入保活。
   也可以前台直接跑：

   ```bash
   python bot.py
   ```

健康检查：

```
GET http://127.0.0.1:8088/health           # 存活
GET http://127.0.0.1:8088/api/status       # 详细状态，需 header X-Bridge-Token
GET http://127.0.0.1:8088/admin?token=...  # 网页控制台
```

## 配置与环境变量

配置在启动时读取一次，改完需要重启进程。`config.example.json` 每一块都带 `_说明` 字段。

常用环境变量（都有默认值，通常不用设）：

| 变量 | 作用 |
|---|---|
| `QQBOT_PYTHON` | keeper 启动 bot 用的解释器，默认当前解释器 |
| `QQBOT_DOCKER_BIN` | docker.exe 路径，默认自动探测 |
| `QQBOT_SOVITS_HOME` | GPT-SoVITS 目录，默认 `~/GPT-SoVITS` |
| `QQBOT_COMFY_WORKFLOWS` | 真机测试用的 ComfyUI 工作流目录 |
| `QQBOT_REV_TEST_IMG` | 反推真机测试用的图片 |
| `QQBOT_NAPCAT_WIN_CFG` | Windows 版 NapCat 配置目录（同步 token 用） |
| `QQBOT_TOOL_DIR` | 安装包缓存目录 |

## 目录结构

| 路径 | 作用 |
|---|---|
| `bot.py` | 主进程：消息路由、AI 大脑、画图/视频、人格、配置注册表、本地 HTTP 服务 |
| `admin_html.py` | `/admin` 控制台页面 |
| `comfy_gen.py` | ComfyUI 出图客户端：工作流拼装、排队、超时、显存卸载 |
| `reverser.py` | 提示词反推 + 图生图链路 |
| `qbot_*.py` | 独立能力模块：记忆、人格、画图、表情包、语音、市场、技能等 |
| `jm_bot.py` / `videoparse.py` / `scramble.py` / `tts.py` | 漫画、视频解析、图片混淆、语音 |
| `qq-mcp-server.py` | stdio MCP server，把 bot 的 `/api/*` 暴露给外部 agent |
| `_oneclick.py` / `_keeper.py` | 一键启动与保活（拉 NapCat、拉 bot、掉了自动重启） |
| `roles/*.md` | 人格文件，可在控制台切换 |
| `docker-napcat/` | NapCat 容器编排、扫码、配置预置 |
| `tests/` | 回归测试，`python tests/run_tests.py` 一键跑 |
| `tools/` | 运维/诊断工具 |
| `docs/` | 长期文档与排查报告 |
| `HANDOVER.md` | 内部作业手册：踩坑史、铁律、排障顺序 |

## 测试

```bash
python tests/run_tests.py          # SAFE 集：不联网、发送与模型调用全部打桩
python tests/run_tests.py --list   # 列出分组；部分用例需要真模型/真服务，需手动跑
```

安全集里有一部分用例会读本机 `config.json` 和 `images/`、`state/` 下的素材，
用来验证配置真的生效。新克隆的仓库需要先按 [tests/README.md](tests/README.md)
准备这些依赖；缺失时相关用例会失败或明确 SKIP。

## 说明

- 仓库里的示例 ID、昵称、路径均已替换为虚构值。
- 本仓库不包含任何真实密钥；`config.json`、`state/`、`images/`、`docker-napcat/.env`
  等运行数据都在 `.gitignore` 中。
- 机器人的行为高度依赖你配置的模型、提示词与人格文件，开箱效果以实际配置为准。

## 许可证

[MIT](LICENSE)
