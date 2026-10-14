// N4 v4 UX browser driver (tidy / collapse / toolbar). Prints ONE JSON line. DOM/console asserts only, no screenshots.
// Usage: PW=<playwright-core path> WB_BASE=http://127.0.0.1:PORT [WB_BOARD=v4-ux] node v4_ux.js   (run against a TEMP COPY of the workbench: it Saves)
const PW = process.env.PW || 'playwright-core';
const { chromium } = require(PW);
const BASE = process.env.WB_BASE, BOARD = process.env.WB_BOARD || 'v4-ux';
const out = {}, errs = [];
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  page.on('pageerror', e => errs.push('pageerror: ' + e));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  const open = async () => { await page.goto(`${BASE}/?board=${BOARD}`); await page.waitForSelector('.card'); await page.waitForFunction(() => window.wbTidy && window.wbCollapse && document.getElementById('v4Tb')); await page.waitForTimeout(500); };
  /* F4 E4: condition waits instead of fixed sleeps (the fixed sleeps lost races when the whole suite runs in parallel). */
  const until = (pg, fn, arg) => pg.waitForFunction(fn, arg, { timeout: 20000 });
  const saveWait = async pg => { const r = pg.waitForResponse(x => /\/board\//.test(x.url()) && x.request().method() !== 'GET', { timeout: 20000 }).catch(() => null); await pg.click('#bSave'); await r; await pg.waitForTimeout(150); };
  const pos = () => page.evaluate(() => JSON.stringify(window.wb.board.cards.map(c => [c.id, c.pos.x, c.pos.y])));
  await open();

  // ---- tidy: idempotent + undo ----
  out.p0 = await pos();
  out.moved1 = await page.evaluate(() => window.wbTidy.run());
  await until(page, a => JSON.stringify(window.wb.board.cards.map(c => [c.id, c.pos.x, c.pos.y])) !== a, out.p0);
  out.p1 = await pos();
  out.moved2 = await page.evaluate(() => window.wbTidy.run());
  out.p2 = await pos();
  out.canUndo = await page.evaluate(() => window.wbTidy.canUndo());
  out.undoOk = await page.evaluate(() => window.wbTidy.undo());
  out.pUndo = await pos();
  out.onGrid = await page.evaluate(() => { window.wbTidy.run(); return window.wb.board.cards.every(c => c.pos.x % 8 === 0 && c.pos.y % 8 === 0) });
  out.tidyMoved3 = await page.evaluate(() => window.wbTidy.run());
  // column order sanity: a1 left of a2 left of a3
  out.cols = await page.evaluate(() => { const g = id => window.wb.board.cards.find(c => c.id === id).pos.x; return [g('a1') < g('a2'), g('a2') < g('a3'), g('x1') < g('a1'), g('a3') < g('b1')] });

  // ---- collapse ----
  await open();
  const snap = () => page.evaluate(() => JSON.stringify({ cards: window.wb.board.cards, edges: window.wb.board.edges, keys: Object.keys(window.wb.board).filter(k => k !== 'group_styles').sort() }));
  out.before = await snap();
  out.lsBefore = await page.evaluate(() => JSON.stringify(Object.keys(localStorage).sort()));
  out.expo = await page.evaluate(() => window.wbCollapse.exposed('A'));
  await page.evaluate(() => window.wbCollapse.set('A', true));
  await until(page, () => window.wbCollapse.isCollapsed('A') && !!window.wb.board.group_styles && !!document.querySelector('.grp[data-group="A"].cx-collapsed') && getComputedStyle(document.querySelector('.card[data-id="a1"]')).visibility === 'hidden' && document.querySelectorAll('#edges path.cx').length > 0);
  out.gs = await page.evaluate(() => window.wb.board.group_styles);
  out.after = await snap();
  out.lsAfter = await page.evaluate(() => JSON.stringify(Object.keys(localStorage).sort()));
  out.dom = await page.evaluate(() => {
    const grp = document.querySelector('.grp[data-group="A"]');
    const hid = [...document.querySelectorAll('.card')].filter(c => ['a1', 'a2', 'a3'].includes(c.dataset.id)).map(c => getComputedStyle(c).visibility);
    const paths = [...document.querySelectorAll('#edges path.e')];
    return { cls: grp.classList.contains('cx-collapsed'), chips: [...grp.querySelectorAll('.cx-chip')].map(c => c.dataset.side + ':' + c.dataset.ep),
      hid, hiddenCore: paths.filter(p => p.style.display === 'none').length, coreN: paths.length, overlay: [...document.querySelectorAll('#edges path.cx')].map(p => p.dataset.from + '>' + p.dataset.to),
      otherVisible: ['x1', 'b1', 'b2'].map(i => getComputedStyle(document.querySelector('.card[data-id="' + i + '"]')).visibility) };
  });
  // toggle button expands
  await page.click('.grp[data-group="A"] .cx-tg');
  await until(page, () => !window.wbCollapse.isCollapsed('A') && !document.querySelector('.grp[data-group="A"].cx-collapsed') && document.querySelectorAll('.card.cx-hid').length === 0 && document.querySelectorAll('#edges path.cx').length === 0);
  out.expandedGs = await page.evaluate(() => window.wb.board.group_styles === undefined ? 'undefined' : JSON.stringify(window.wb.board.group_styles));
  out.expandedCls = await page.evaluate(() => document.querySelector('.grp[data-group="A"]').classList.contains('cx-collapsed') + '/' + document.querySelectorAll('.card.cx-hid').length + '/' + document.querySelectorAll('#edges path.cx').length);
  out.afterExpand = await snap();

  // persistence through Save + reload
  await page.evaluate(() => window.wbCollapse.set('B', true));
  await saveWait(page);
  out.saved = await page.evaluate(async b => { const r = await fetch('/board/' + b); const j = await r.json(); return JSON.stringify(j.group_styles || null) }, BOARD);
  await open();
  out.reloaded = await page.evaluate(() => JSON.stringify(window.wb.board.group_styles || null) + '|' + document.querySelector('.grp[data-group="B"]').classList.contains('cx-collapsed'));
  await page.evaluate(() => window.wbCollapse.setAll(false));
  out.setAllCleared = await page.evaluate(() => window.wb.board.group_styles === undefined);
  await page.evaluate(() => window.wbCollapse.setAll(true));
  out.setAllN = await page.evaluate(() => window.wbCollapse.collapsedGroups().join(','));
  await page.evaluate(() => window.wbCollapse.setAll(false));
  await saveWait(page);   // leave the temp board expanded

  // ---- shortcuts: select.js still owns Ctrl+A / Ctrl+C; Alt+T / Alt+L are ours ----
  await open();
  await page.evaluate(() => { document.activeElement && document.activeElement.blur && document.activeElement.blur() });
  await page.keyboard.press('Control+a'); await until(page, () => window.wb.sel.size > 1);
  out.ctrlA = await page.evaluate(() => window.wb.sel.size + '/' + window.wb.board.cards.length);
  await page.evaluate(() => localStorage.removeItem('wb.clipboard'));
  await page.keyboard.press('Control+c'); await until(page, () => !!localStorage.getItem('wb.clipboard'));
  out.ctrlC = await page.evaluate(() => !!localStorage.getItem('wb.clipboard'));
  await page.evaluate(() => { window.wb.sel.clear(); window.wb.sel.add('b1'); window.wb.render() });
  await page.keyboard.press('Alt+l'); await until(page, () => window.wbCollapse.isCollapsed('B'));
  out.altL = await page.evaluate(() => window.wbCollapse.isCollapsed('B'));
  await page.keyboard.press('Alt+Shift+L'); await until(page, () => window.wbCollapse.collapsedGroups().length === 0);
  out.altShiftL = await page.evaluate(() => window.wbCollapse.collapsedGroups().length);
  await page.evaluate(() => { window.wb.sel.clear(); window.wb.render() });
  await page.evaluate(() => { const b = window.wb.board; b.cards.find(c => c.id === 'a3').pos = { x: 8, y: 8 }; window.wb.render() });
  await page.keyboard.press('Alt+t'); await until(page, () => window.wbTidy.canUndo());
  out.altT = await page.evaluate(() => window.wbTidy.canUndo());
  await page.keyboard.press('Alt+z'); await until(page, () => !window.wbTidy.canUndo());
  out.altZ = await page.evaluate(() => !window.wbTidy.canUndo() && window.wb.board.cards.find(c => c.id === 'a3').pos.x === 8);
  // desktop toolbar buttons exist and are visible in the bar
  out.deskTb = await page.evaluate(() => [...document.querySelectorAll('#v4Tb button')].map(b => { const r = b.getBoundingClientRect(); return b.id + ':' + (r.width > 0 && r.height > 0) }));
  await ctx.close();

  // ---- phone 390px ----
  const pctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  const ph = await pctx.newPage();
  ph.on('pageerror', e => errs.push('phone pageerror: ' + e));
  ph.on('console', m => { if (m.type() === 'error') errs.push('phone console: ' + m.text()); });
  await ph.goto(`${BASE}/?board=${BOARD}`); await ph.waitForSelector('.card');
  await ph.waitForFunction(() => window.wbTidy && document.getElementById('v4Tb') && window.wbCollapse && document.querySelectorAll('.card').length > 0); await ph.waitForTimeout(600);
  out.phone = await ph.evaluate(() => {
    const de = document.documentElement, vw = window.innerWidth;
    const btns = [...document.querySelectorAll('#v4Tb button')].map(b => { const r = b.getBoundingClientRect(); return { id: b.id, l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height), disp: getComputedStyle(b).display } });
    const tb = document.getElementById('v4Tb').getBoundingClientRect();
    return { vw, scrollW: Math.max(de.scrollWidth, document.body.scrollWidth), clientW: de.clientWidth, btns, tbBottom: Math.round(tb.bottom), vh: window.innerHeight, pos: getComputedStyle(document.getElementById('v4Tb')).position };
  });
  // all actions reachable by touch: tap each; page must not error
  await ph.evaluate(() => { window.__msgs = []; const m = document.getElementById('msg'); new MutationObserver(() => window.__msgs.push(m.textContent)).observe(m, { childList: true, characterData: true, subtree: true }); });
  await ph.tap('#v4Tidy'); await until(ph, () => window.__msgs.some(t => /tidied|already tidy/.test(t)));
  out.phoneTidyMsg = await ph.evaluate(() => window.__msgs.find(t => /tidied|already tidy/.test(t)));
  await ph.tap('#v4CollapseAll'); await until(ph, () => window.wbCollapse.collapsedGroups().length === 2);
  out.phoneCollapsed = await ph.evaluate(() => window.wbCollapse.collapsedGroups().length);
  await ph.tap('.grp[data-group="A"] .cx-tg'); await until(ph, () => !window.wbCollapse.isCollapsed('A'));
  out.phoneToggle = await ph.evaluate(() => window.wbCollapse.isCollapsed('A'));
  await ph.tap('#v4ExpandAll'); await until(ph, () => window.wbCollapse.collapsedGroups().length === 0);
  out.phoneExpanded = await ph.evaluate(() => window.wbCollapse.collapsedGroups().length);
  out.phoneScrollAfter = await ph.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth);
  await pctx.close();
  await browser.close();
  out.errs = errs;
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ fatal: String(e && e.stack || e), errs })); process.exit(0); });
