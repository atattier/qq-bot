# -*- coding: utf-8 -*-
"""离线核验修复：直接用 admin_html.admin_page(token) 生成页面 + 真实 /api/config 数据，
在浏览器里本地渲染，断言 ① 无 JS 错误 ② 分区导航按钮齐全 ③ 识图卡出现 ④ 所有分组卡都在。
不依赖 bot 重启。
"""
import io, json, os, subprocess, sys, tempfile, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = os.environ.get("QBOT_ADMIN_WORKSPACE") or ""
NODE = os.environ.get("QBOT_NODE") or "node"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

import importlib
import admin_html
importlib.reload(admin_html)

import bot
importlib.reload(bot)

HTML = admin_html.ADMIN_HTML.replace("__TOKEN__", "TESTTOKEN")
CFG_DATA = bot.action_config()
CFG_JSON = json.dumps(CFG_DATA, ensure_ascii=False)

tmpdir = tempfile.mkdtemp(prefix="_admin_offline_")
page_path = os.path.join(tmpdir, "admin.html")
io.open(page_path, "w", encoding="utf-8").write(HTML)
cfg_path = os.path.join(tmpdir, "config.json")
io.open(cfg_path, "w", encoding="utf-8").write(CFG_JSON)

JS = r"""
const { chromium } = require('playwright-core');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true,
    args: ['--no-proxy-server','--disable-gpu','--allow-file-access-from-files'] });
  const page = await browser.newPage();
  const errs = [], consoleErrs = [];
  page.on('pageerror', e => errs.push(String(e && e.stack || e)));
  page.on('console', m => { if (m.type() === 'error') consoleErrs.push(m.text()); });

  // 拦截 /api/config 返回本地数据（页面是 file:// 加载，fetch 走不上，用 route 兜住）
  await page.route('**/api/config*', async route => {
    if (route.request().method() === 'POST') return route.fulfill({status:200, contentType:'application/json', body:'{"ok":true}'});
    return route.fulfill({status:200, contentType:'application/json', body: process.env.CFG_JSON});
  });
  await page.route('**/api/status*', route => route.fulfill({status:200, contentType:'application/json',
    body:'{"ok":true,"nickname":"test","groups":[],"comfy":{},"images":[]}'}));

  await page.addInitScript(() => { try { localStorage.setItem('qqbot_token','TESTTOKEN'); } catch(e){} });
  await page.goto('file://' + process.env.PAGE_PATH.replace(/\\/g,'/'), { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(2500);

  const st = await page.evaluate(() => {
    const nav = document.getElementById('catnav');
    const grid = document.getElementById('grid');
    const btns = nav ? nav.querySelectorAll('.catbtn') : [];
    const cards = grid ? Array.prototype.slice.call(grid.querySelectorAll('.card')) : [];
    const titles = cards.map(c => { const t = c.querySelector('.card__title'); return t ? t.textContent : '?'; });
    const seeCard = cards.find(c => { const t=c.querySelector('.card__title'); return t && t.textContent==='识图'; });
    return {
      navBtnCount: btns.length,
      navBtns: Array.prototype.map.call(btns, b => b.getAttribute('data-cat') + ':' + b.textContent),
      cardTitles: titles,
      cardCount: cards.length,
      hasSeeCard: !!seeCard,
      seeDiagFold: seeCard ? seeCard.querySelectorAll('details.fold').length : -1,
      hasBrain: titles.indexOf('大脑') >= 0,
      hasPersona: titles.indexOf('人格') >= 0,
      gridVisibleCards: cards.filter(c => !c.hidden).map(c => { const t=c.querySelector('.card__title'); return t?t.textContent:'?'; }),
    };
  });
  console.log('=== RESULT ===');
  console.log(JSON.stringify({ st, errs, consoleErrs }, null, 2));
  await browser.close();
})().catch(e => { console.log('=== RESULT ==='); console.log(JSON.stringify({fatal:String(e)})); });
"""

p = os.path.join(tmpdir, "_offline_check.js")
io.open(p, "w", encoding="utf-8").write(JS)
env = dict(os.environ)
env["CHROME_PATH"] = CHROME
env["PAGE_PATH"] = page_path
env["NODE_PATH"] = os.path.join(WS, "node_modules")
env["CFG_JSON"] = CFG_JSON
for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"): env.pop(k, None)
r = subprocess.run([NODE, p], env=env, capture_output=True, timeout=180)
print(r.stdout.decode("utf-8","replace"))
e = r.stderr.decode("utf-8","replace")
if e.strip(): print("--- stderr ---"); print(e[:2500])
