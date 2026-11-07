// 素材市场截图自检（2026-10-27）：验证「点一下不闪」「格子排得下按钮」「放大层有样式」
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
// 从仓库根跑：node tools/_shot_market.js（路径都相对 ROOT，别处跑也不会找不到 token）
const ROOT = path.join(__dirname, '..');

const TOKEN = fs.readFileSync(path.join(ROOT, 'state/bridge-token.json'), 'utf8').match(/"token"\s*:\s*"([^"]+)"/)[1];
const URL = 'http://127.0.0.1:8088/admin?token=' + encodeURIComponent(TOKEN);

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.QBOT_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--no-proxy-server'],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1.5 });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  const bad = [];
  page.on('response', r => { if (r.url().includes('/api/petpet') && r.status() >= 400) bad.push(r.status() + ' ' + r.url().slice(0, 90)); });

  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(1200);

  // 找「捏脸」那一行，点它的「配置」
  const opened = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.prow'));
    const r = rows.find(x => /捏脸/.test(x.textContent));
    if (!r) return 'no-row:' + rows.length;
    const b = Array.from(r.querySelectorAll('button')).find(x => /配置|设置/.test(x.textContent));
    if (!b) return 'no-btn';
    b.click();
    return 'ok';
  });
  console.log('打开抽屉:', opened);
  await page.waitForTimeout(2500);

  // 抽屉滚到素材市场
  await page.evaluate(() => {
    const g = document.querySelector('.mgrid--mk');
    if (g) g.scrollIntoView({ block: 'center' });
  });
  await page.waitForTimeout(2500);

  const stat = await page.evaluate(() => {
    const t = document.querySelector('.drawer__body .moodtip');
    const g = document.querySelector('.mgrid--mk');
    const imgs = g ? Array.from(g.querySelectorAll('img')) : [];
    return {
      tip: t ? t.textContent.trim() : '',
      cells: g ? g.querySelectorAll('.mcell').length : 0,
      demos: imgs.filter(i => i.src.includes('/api/petpet_demo')).length,
      frames: imgs.filter(i => i.src.includes('/api/petpet_frame')).length,
      broken: imgs.filter(i => i.complete && i.naturalWidth === 0).length,
      cellW: g && g.firstElementChild ? Math.round(g.firstElementChild.getBoundingClientRect().width) : 0,
      btnW: g && g.querySelector('.mcell .btn') ? Math.round(g.querySelector('.mcell .btn').getBoundingClientRect().width) : 0,
    };
  });
  console.log('市场状态:', JSON.stringify(stat));

  await page.screenshot({ path: path.join(ROOT, 'state/_mk_grid.png') });

  // 点第一张图 → 放大层
  const clicked = await page.evaluate(() => {
    const im = document.querySelector('.mgrid--mk img');
    if (!im) return false;
    im.click();
    return true;
  });
  await page.waitForTimeout(700);
  const z = await page.evaluate(() => {
    const b = document.querySelector('.zoom');
    if (!b) return null;
    const cs = getComputedStyle(b);
    const im = b.querySelector('img');
    const r = b.getBoundingClientRect();
    return {
      pos: cs.position, z: cs.zIndex, bg: cs.backgroundColor,
      w: Math.round(r.width), h: Math.round(r.height),
      imgOk: im ? im.naturalWidth > 0 : false,
      imgW: im ? Math.round(im.getBoundingClientRect().width) : 0,
    };
  });
  console.log('放大层:', clicked, JSON.stringify(z));
  await page.screenshot({ path: path.join(ROOT, 'state/_mk_zoom.png') });

  // Esc 关掉
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  const closed = await page.evaluate(() => !document.querySelector('.zoom'));
  console.log('Esc 关掉放大层:', closed);

  console.log('接口 4xx/5xx:', bad.length ? bad.slice(0, 6) : '无');
  console.log('页面报错:', errs.length ? errs.slice(0, 6) : '无');
  await browser.close();
})();
