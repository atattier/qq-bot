// 捏脸表情包（petpet）—— 把头像做成「摸摸头 / 揉一揉 / 咬一口」那套动图
//
// ── 这个技能和别的技能不一样：index.js 只是清单占位，真正干活的是 Python ──────────
//   whale-memes / trendy-memes 的 index.js 是原 QQ Agent 的 Node 模块，本机不执行，
//   只被 qbot_skills._tool_names() 嗅一下「注册了哪几个工具」用于后台展示。
//   petpet 也一样：合成算法在 qbot_petpet.py（Pillow 重写），工具在
//   qbot_skill_tools.py（petpet.send / petpet.list），群口令直通在 bot.handle_petpet()。
//   下面两个 registerTool 只为让后台「技能」页显示工具名，删了不影响功能。
//
// ── 素材 ────────────────────────────────────────────────────────────────
//   skills/petpet/assets/<模板>/<帧号>.png，来自 nonebot-plugin-petpet（MIT）。
//   缺素材跑：python tools/_fetch_petpet_assets.py
//   加模板：qbot_petpet.py 的 TEMPLATES 里加一条（帧参数照抄上游 functions.py）。

export const promptSections = [];

export function setup(api) {
  api.registerTool({
    name: 'petpet_send',
    description: '把某个群友的头像做成捏脸动图发到当前会话',
    parameters: { group_id: 'number', qq: 'string', template: 'string' },
  });
  api.registerTool({
    name: 'petpet_list',
    description: '列出可用的捏脸模板',
    parameters: {},
  });
}
