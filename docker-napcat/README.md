# 方案 B：NapCat 跑进 Docker（Linux 无头 QQ）

> 背景：小鲸号在 Windows 上跑的是**注入版** NapCat（`launcher-win10.bat` 注入官方 `QQ.exe`）。
> 腾讯安全中心已把这台设备按「**设备存在外挂或其他软件影响 QQ 正常使用**」处理 ⇒
> 服务端周期性作废登录票据 ⇒ **每 ~2 小时被踢一次**（实测 1h40m / 1h54m，10-22 00:35 又一次）。
> 配置层已经没有牌可打（快速登录 `-q`、密码登录、重启都试过，全被挡），所以换**路线**：
> 不再注入 Windows 官方 QQ，改成容器里跑 **Linux 无头 QQ** —— 换掉设备指纹。

---

## 一、动手清单（只有 2 个动作 + 1 次扫码）

| # | 动作 | 说明 |
|---|---|---|
| 1 | 双击 **`docker_go.bat`** | 自动提权 → 开 WSL2/虚拟机平台 → 装 Docker Desktop → 提示**重启** |
| 2 | **重启电脑**，再双击 **`docker_go.bat`** | 自动跳过已完成的步骤 → 起容器 → 接线 → 切 keeper 模式 → 体检 |
| 3 | 扫码（脚本会提示） | `http://127.0.0.1:6099/webui`，口令在 `docker-napcat/.env` 的 `WEBUI_TOKEN` |

`docker_go.bat` 是**可重入**的：跑一半断了、重启了、想重来，再双击一次就行
（进度记在 `docker-napcat/.bootstrap-state.json`；要清零加 `--reset`）。

> ✅ **现状（2026-10-28）**：Docker 已装好且是**唯一跑法**，本清单只在重建环境时用。
> 日常启动请用 `start_qq.bat` / `start_all.bat`；Docker Desktop 没跑时由主人双击桌面 Docker Desktop。

---

## 二、脚本分工（出问题先看这张表）

| 脚本 | 作用 | 要管理员吗 |
|---|---|---|
| `docker_go.bat` | 总入口：全部阶段串起来跑（幂等） | 要（自动提权） |
| `install_docker.bat` | 只做「装环境」两段（features + install） | 要 |
| `docker_doctor.bat` | **体检**：QQ→容器→bot 全链路逐环探活，断哪环说哪环 | 不要 |
| `docker_status.bat` | 容器状态 + 3000 端口 + 账号 online | 不要 |
| `docker_up.bat` / `docker_down.bat` | 起 / 停容器（登录态在卷里，不丢） | 不要 |
| `docker_wire.bat` | 只做接线（改 bot 的 listen_host + NapCat 的上报地址） | 不要 |

对应 Python 实现都在 `tools/`：`docker_bootstrap.py` / `docker_doctor.py` /
`docker_mgr.py`；公共零件在 `docker-napcat/dutil.py`
（四个脚本共用一套「找 docker / 跑命令 / 探端口 / 改 json」逻辑）。

---

## 三、链路与接线（成败关键）

```
QQ 服务器 ⇄ 容器内 NapCat ──①上报──▶ host.docker.internal:8088/onebot ⇄ bot(8088)
                    ▲                                                     │
                    └────────────②调用 http://127.0.0.1:3000 ◀─────────────┘
```

- **①容器 → bot**：容器是独立网络命名空间，`127.0.0.1` 指的是它自己。
  所以 NapCat 的 HTTP 客户端地址必须是 `host.docker.internal:8088/onebot`
  （Docker Desktop 内置域名，IP 变了也不用改）。
- **②bot 监听**：`config.json` 的 `listen_host` 从 `127.0.0.1` 改成
  **`127.0.0.1,<容器网关IP>`**（逗号分隔，形如 `172.25.0.1`）。
  bot.py 会为每个地址各绑一个 socket：
  - 留 `127.0.0.1` 是因为 keeper 探活、`/admin` 后台、回归测试全走它；
  - 加网关 IP 是让容器能回连；
  - **不写 `0.0.0.0`**：`/onebot` 即使校验 token，也不该暴露到全接口；
    公网/不可信网络能连到就等于多一层风险。
- **上报 token**：`config.json` 的 `napcat_report_token` 必须来自 NapCat 的
  `httpClients.token`（不是 `httpServers.token`，也不是 `config.json` 的 `token`）。
  `docker_wire.bat` 会从容器配置自动同步；留空时 `/onebot` 沿用旧兼容判据并告警。
- **③bot → 容器**：compose 把容器 3000 映射到宿主机 3000，`napcat_http` 不用改。

网关 IP 每次重启可能变（WSL NAT 重新分配）。变了会怎样：容器上报打不进来，
bot 本机一切正常 ⇒ 表现是「bot 活着但群里没反应」。`docker_doctor.bat` 会直接指出这一环，
重跑 `docker_wire.bat` 即可（它会追加新 IP、保留旧的，不会重复写）。

---

## 四、只有一种跑法（2026-10-26 起）

NapCat **只跑 Docker 容器版**。原来的 `inject`（Windows 注入官方 QQ.exe）
已被主人砍掉，整条代码路径（起来、杀进程树、开二维码图片、`napcat.mode`
切换、`docker_rollback`）全部删除，`config.json` 的 `napcat.mode` /
`napcat.docker_fallback` 两个键也退役了 —— 留着不读、不生效。

| 场景 | keeper 的动作 |
|---|---|
| Docker 守护进程不在 | 尝试拉起 Docker Desktop，每 60s 重试；**只等，没有替代方案** |
| 容器挂了 | `docker compose up -d` 重拉（`LAUNCH_COOLDOWN` 300s 内最多一次） |
| 账号被踢/离线 | **绝不重启容器** —— 见下 |
| 发送通道 1006514 | `docker restart` 重建长连接 |

🔴 「账号离线」**故意不自愈重启**：被踢是腾讯服务端作废票据，重启容器不会让票据复活，
反而会把 WebUI 上正等人扫的那张码刷掉。所以动作是：**把扫码页摆到人面前，然后等**。

**没有「恢复注入版」这个选项**：旧代码只在 git 历史和 `_archive/inject-era-20261026/`，
且腾讯已按版本硬门槛拒登旧版（错误码 45 实测两次）—— 回去只会彻底登不上。

---

## 五、没有「两套抢 Token」这回事了

以前注入版和容器版会抢同一个账号的 Token。现在只剩容器版，这条风险自动消失。
你自己在宿主机上登的桌面 QQ（`E://QQ//QQ.exe`）不受影响 —— 它和容器是两个客户端。

---

## 六、首次扫码（只需一次）

```
http://127.0.0.1:6099/webui          # 口令见 docker-napcat/.env 的 WEBUI_TOKEN
```

登录态写进 `docker-napcat/.config` 这个卷，之后容器重启/重建都不用再扫。
`docker-napcat/config/onebot11_<QQ>.json` 由 `wire.py` 预置（上报地址、token 都填好），
所以第一次起来就自带正确的网络配置 —— 不用再进 WebUI 手改。

⚠️ 如果腾讯要求短信验证码，**不要再试密码登录**，那条路已确认被封，走扫码。

---

## 七、验收标准（什么算「成了」）

`docker_doctor.bat` 全绿，具体是这 11 项：

```
✅ docker 守护进程     ✅ napcat 容器(running)   ✅ 容器自启策略(always)
✅ 宿主机 3000         ✅ WebUI 6099            ✅ 账号在线
✅ 容器→宿主机         ✅ bot 127.0.0.1:8088    ✅ bot /health
✅ bot 监听含网关IP    ✅ 上报地址               ✅ keeper 保活
```

「容器→宿主机」那一项是真在容器里发 HTTP 请求（`docker exec … curl/node`），
不是看配置猜的 —— 这一环最容易断，也最难从现象反推。

---

## 八、回滚

**没有「退回注入版」这个操作了**（`docker_rollback.bat` 已归档到
`_archive/inject-era-20261026/`，它唯一作用就是切回 inject）。

现在 Docker 出问题时的处理顺序：
1. `docker_doctor.bat` 体检，看断在哪环
2. `_fix_napcat_stack.cmd`（右键管理员）清 GPU 缓存 + 重拉容器
3. 还是不行就 `docker_down.bat` 再 `docker_up.bat`
登录态（`.config` 卷）不会丢，不用重新扫码。

---

## 九、没有 Docker 时怎么验（`tools/docker_selftest.py`）

把 docker / 端口 / 账号探活全换成假的，真跑一遍 wire / doctor / docker_mgr /
bootstrap，断言它们改出来的配置和给出的结论都对。**不需要装 Docker**，
也不碰真 `config.json`。已并入日常回归：`python tests/run_tests.py`（`_tdocker.py`）。

```bash
python tools/docker_selftest.py       # 14 个用例
python tools/docker_selftest.py -v    # 看每一步的假 docker 命令
```

---

## 十、自建镜像补丁（2026-11-03：引用解析）

`mixed/Dockerfile` 除了把 QQ 升到 3.2.34，还会跑 `patch_napcat_reply.py` 给
`/app/napcat/napcat.mjs` 打一个引用解析补丁。**不修的话**：引用一条查不到记录的
消息（实测是机器人自己发的 3.7k 字长提示词）再发「画个」，NapCat 会把 reply 段
整个丢掉，bot 收到 `qmid=0`，只能当无源指令乱画。

补丁做两件事（都幂等，锚点找不到会让构建直接失败）：
1. `replyElement` 第 4 个参数按选项对象解构，恢复 `quick_reply` 的布尔语义
   （4.15.19 把选项对象当 true，方法 1 查不到就直接放弃，方法 2/3 永远不跑）；
2. 三条查询全失败时，用事件自带的 `records.msgId` 兜底生成 reply 段。

现成容器已经热打过，日志里出现下面这行就是兜底生效：
`引用解析失败，回退用事件自带记录生成引用段`。
容器里留了原文件备份 `/app/napcat/napcat.mjs.bak-reply-20261103`；
重建镜像时补丁自动重打，**不需要**手工再进容器。
