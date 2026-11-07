# 捏脸表情包（petpet）

> 群友发「摸摸头 @某人」，机器人把那个人的头像做成动图发出来。
> 装进 `skills/petpet/` 即生效（热加载，不用重启）；素材缺失时跑一次下载脚本。

这是开源项目 [nonebot-plugin-petpet](https://github.com/noneplugin/nonebot-plugin-petpet)（MIT）的**本地移植**。

---

## 为什么不是直接 `pip install`

本机 bot **不是 NoneBot**：`bot.py` 是纯标准库 + Pillow 的单进程，直接收 NapCat 的 OneBot11 HTTP POST。
`nonebot-plugin-petpet` 是 NoneBot2 插件，装了也只会躺在 site-packages 里 —— 它要求
`nonebot2 + nonebot-adapter-onebot + nonebot-plugin-imageutils` 这一整套运行时，
而且工具要靠 `nonebot.on_command` 注册到事件总线，本机根本没有那条总线。

所以这里只搬了**真正有价值的两样**：

| 上游的东西 | 本机怎么处理 |
|---|---|
| 模板素材（`resources/images/<模板>/*.png`） | 下载到 `skills/petpet/assets/`，按 md5 校验 |
| 合成算法（`functions.py` 里每个模板的帧位置/缩放/旋转） | 用 Pillow 重写成 `qbot_petpet.py` 的 `TEMPLATES` 表 |
| 命令注册 / 事件总线 / 额度 / 下载 | 换成项目自己的：群口令直通 + `qbot_skill_tools` 工具 + 技能参数 |

---

## 能用哪些动作

| 口令 | 模板 id | 帧数 |
|---|---|---|
| 摸摸头 / 摸摸 / 揉头 / rua / petpet | `petpet` | 5 |
| 拍拍 / 拍一拍 / 拍头 | `pat` | 28（10 张图拼的序列） |
| 揉一揉 / 揉揉 / 搓一搓 | `rub` | 6（要用到发起人自己的头像） |
| 咬一口 / 咬咬 / 啃 | `bite` | 16 |
| 敲一敲 / 敲门 | `knock` | 8 |
| 揍一拳 / 揍 / 打一拳 | `punch` | 13 |
| 捶 / 捶一捶 / 锤 | `pound` | 8 |
| 撞 / 撞一撞 | `thump` | 4 |
| 滚一圈 / 滚 | `roll` | 8 |
| 扭一扭 / 扭 | `twist` | 5 |
| 扔出去 / 扔 / 抛 | `throw_gif` | 8 |
| 猫猫揉 / capoo | `capoo_rub` | 4 |
| 支持 / 支持你 | `support` | 1（jpg） |

口令要出现在**句首**、整句不超过 12 个字才会触发（`我咬了一口苹果` 不会误触）。
单字动作（咬/揉/滚/捶/撞/扭/扔/抛）必须带 @ 才算（`揉眼睛` 不算）。

**记不住动作名？** 群里 @ 一下机器人说「模板」，会列出带编号的全部互动；回「模板 3」直接用第 3 个动作给你自己（或你 @ 的人）做一张。

---

## 三条触发路径

1. **群口令直通**（`bot.handle_petpet` / `handle_petpet_list`）：不过大脑。命中就出图，模型不会把「摸摸头」当寒暄回掉；「@我 模板」直接列清单和编号。
2. **大脑主动**：MCP 的 `qq_petpet` 工具 → `POST /api/skill` → `petpet.send`。
3. **后台/脚本**：`POST /api/skill {"skill":"petpet","tool":"send","args":{"cid":群号,"qq":"123","template":"pat"}}`。

目标头像的优先级：**本条消息带的图** > **@ 的第一个人** > **发话人自己**。

---

## 参数（`/admin` 或群口令「设置」可改）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 总开关，关掉连口令都不认 |
| `proactive` | true | 允许大脑主动出图 |
| `maxPerHour` | 20 | 每个会话每小时最多几张 |
| `cooldown` | 6 | 同一会话冷却秒数 |
| `defaultTemplate` | petpet | 没说动作时用哪个 |

状态落在 `state/skills/petpet/state.json`（每会话最近一小时的发送时间戳，用于额度）。
渲染出来的图落在 `cache/petpet/`，超过 120 个或 24 小时自动清。

---

## 加模板 / 补素材

```bash
# 补默认那 13 个模板
python tools/_fetch_petpet_assets.py
# 只要某几个
python tools/_fetch_petpet_assets.py petpet pat rub
# 全下（99 个模板，1091 个文件，很慢）
python tools/_fetch_petpet_assets.py --all
```

直连 `raw.githubusercontent.com` 在本机会 SSL EOF，脚本按顺序试 `ghproxy.net` / `gh-proxy.com` /
`gcore.jsdelivr.net` 三个镜像，并且用 `resource_list.json` 里的 md5 校验（镜像偶尔会塞错误页）。

加一个新模板：在 `qbot_petpet.py` 的 `TEMPLATES` 里加一条，帧参数照抄上游
`functions.py` 对应函数（`locs` 的元组顺序是 `(x, y, w, h)`，`capoo_rub` 例外是 `(w, h, x, y)`）。
