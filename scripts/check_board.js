// Headless readability check. node scripts/check_board.js --base http://127.0.0.1:8765 (--board NAME ... | --all) [--out DIR] [--keep 3] [--no-shot]
// Opens each board in the REAL canvas (Chromium via playwright-core, same PW path as tests/run_all.py), waits for fonts, runs the pure
// canvas/js/readability.js check on what is drawn (card boxes, lane frames, rendered line paths), saves a fit-to-view screenshot per board
// (never read by vision: the geometry is the verdict) and prints ONE JSON document. Exit 1 if any board fails (unless --no-fail).
const fs = require('fs'), path = require('path');
const PW = process.env.PW || 'playwright-core';
const { chromium } = require(PW);
const a = process.argv.slice(2), opt = (k, d) => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : d; };
const BASE = opt('--base', process.env.WB_BASE || 'http://127.0.0.1:8765');
const OUT = opt('--out', path.join(__dirname, '..', 'runs', 'readability'));
const KEEP = +opt('--keep', 3), SHOT = !a.includes('--no-shot');
let boards = a.flatMap((x, i) => (x === '--board' ? [a[i + 1]] : []));

(async () => {
  if (a.includes('--all') || !boards.length) boards = (await (await fetch(BASE + '/boards')).json()).boards.filter(b => b !== 'active');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1200 }, deviceScaleFactor: 1 });
  if (a.includes('--legacy')) await ctx.addInitScript(() => { window.__wbLegacy = true; });
  const results = [];
  for (const b of boards) {
    const page = await ctx.newPage(); const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    try {
      await page.goto(BASE + '/?board=' + b, { waitUntil: 'load' });
      await page.waitForSelector('#cards .card', { timeout: 20000 });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => window.wbReadability && window.wb && window.wb.board, null, { timeout: 20000 });
      await page.waitForTimeout(700);
      const r = await page.evaluate(() => window.wbReadability.measure());
      if (SHOT) {
        const dir = path.join(OUT, b); fs.mkdirSync(dir, { recursive: true });
        const f = path.join(dir, new Date().toISOString().replace(/[:.]/g, '-') + '.png');
        await page.screenshot({ path: f }); r.shot = f.replace(/\\/g, '/');
        const old = fs.readdirSync(dir).filter(x => x.endsWith('.png')).sort(); while (old.length > KEEP) fs.unlinkSync(path.join(dir, old.shift()));
      }
      if (errs.length) r.page_errors = errs.slice(0, 3);
      results.push(r);
    } catch (e) { results.push({ board: b, pass: false, error: String(e).slice(0, 200), overlaps: [], crossings: [] }); }
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify({ base: BASE, ts: new Date().toISOString(), pass: results.every(r => r.pass), boards: results }, null, 1));
  process.exit(results.every(r => r.pass) || a.includes('--no-fail') ? 0 : 1);
})().catch(e => { console.error('SCRIPT ERROR ' + e); process.exit(2); });
