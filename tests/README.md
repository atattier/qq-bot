# 回归测试

```bash
python tests/run_tests.py          # SAFE 集：离线、发送与模型调用全部打桩
python tests/run_tests.py --list   # 列出 SAFE / MANUAL 分组
python tests/run_tests.py --only _tdraw _tset
```

## 环境说明

SAFE 集不联网、不启常驻服务，但其中一部分用例会**读取本机的 `config.json` 与运行数据**，
用来验证「配置项确实生效」而不是拿假值自证。新克隆的仓库只有 `config.example.json`，
因此下面这些用例需要先准备环境：

| 依赖 | 影响的用例 | 准备方式 |
|---|---|---|
| `config.json` 里的模型 / 画图档 / 群号 / 白名单 | `_tset` `_tsee_follow` `_tomni` `_tnotify` `_troute` `_tvg` `_tengine` `_tentry` `_tat` `_tscramble` `_tcover` | 复制 `config.example.json` 为 `config.json` 并按自己的环境填写 |
| ComfyUI 工作流目录 | `_tset` `_tvg` | 配置 `comfyui.dir`，或把工作流放进 `user/default/workflows` |
| `images/`、`images/emoji/` 素材 | `_tsim` `test_skill_edit` | 放任意图片素材（图库与表情包目录） |
| `state/memewiki/memes.db` | `test_skill_edit` | 先用梗百科工具构建本地词库 |
| `state/jm_favorites.json` | `_tjm` | 先用 `tools/_jm_fav_browser.py` 抓一次收藏 |
| `docker-napcat/.env` | `_tdocker` `_tqr` | `cp docker-napcat/.env.example docker-napcat/.env` |
| DSH CLI（`dsh.cmd`） | `_tdsh` | 安装后设 `QBOT_DSH_CLI`；未安装时该检查会 SKIP |
| git 历史基线 | `_tu12e` `_tu12b` | 公开快照是单提交，这两个用例会打印 SKIP |

环境缺失时，相关用例会明确 SKIP（例如 DSH CLI、梗百科词库、git 基线）；
其余用例直接失败并打印缺哪一项，不会静默跳过。

`MANUAL` 组会真的调用模型、花钱或启动进程，只在改动相关区域时手动跑。
