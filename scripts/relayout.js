// Auto-arrange boards with ELK in the real canvas and (with --save) persist ONLY layout keys through PATCH /board/<name>/visual
// (the server returns 409 if the semantic hash would change, so meaning cannot move).
// node scripts/relayout.js --base URL (--board NAME ... | --all) [--save]       prints one JSON line per board; exit 1 if a board is not clean after the layout
const PW = process.env.PW || 'playwright-core';
const { chromium } = require(PW);
const a = process.argv.slice(2), opt = (k, d) => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : d; };
const BASE = opt('--base', process.env.WB_BASE || 'http://127.0.0.1:8765'), SAVE = a.includes('--save');
let boards = a.flatMap((x, i) => (x === '--board' ? [a[i + 1]] : []));
(async () => {
  if (a.includes('--all') || !boards.length) boards = (await (await fetch(BASE + '/boards')).json()).boards.filter(b => b !== 'active');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1200 } });
  let bad = 0;
  for (const b of boards) {
    const page = await ctx.newPage();
    await page.goto(BASE + '/?board=' + b); await page.waitForSelector('#cards .card'); await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => window.wbAutoLayout && window.wbReadability); await page.waitForTimeout(500);
    const r = await page.evaluate(async (save) => {
      const plan = await window.wbAutoLayout.compute(); const moved = window.wbAutoLayout.apply(plan);
      await new Promise(r => setTimeout(r, 600)); const m = window.wbReadability.measure(); let patch = null;
      if (save) {
        const body = { cards: plan.cards }; if (plan.group_styles) body.group_styles = plan.group_styles;
        const res = await fetch('/board/' + window.wb.boardName + '/visual', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        patch = res.status;
      }
      return { moved, patch, overlaps: m.overlaps.length, crossings: m.crossings.length, loops: m.loops, routes: m.routes, sample: { o: m.overlaps.slice(0, 4), c: m.crossings.slice(0, 4) } };
    }, SAVE);
    console.log(JSON.stringify(Object.assign({ board: b }, r)));
    if (r.overlaps || r.crossings) bad++;
    await page.close();
  }
  await browser.close(); process.exit(bad ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR ' + e); process.exit(2); });
