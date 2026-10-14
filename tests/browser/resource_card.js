// Fallback resource card browser driver. Prints PASS/FAIL lines.
// Usage: PW=<playwright-core path> WB_BASE=http://127.0.0.1:PORT node resource_card.js   (run against a TEMP COPY: it PUTs a board)
// WB_CHROME=<chrome binary> uses that executable instead of the installed Chrome channel.
const PW = process.env.PW || 'playwright-core';
const { chromium } = require(PW);
const BASE = process.env.WB_BASE, BOARD = 'rc-test';
const errs = [];
let fails = 0;
const ok = (name, cond, info) => { if (!cond) fails++; console.log((cond ? 'PASS ' : 'FAIL ') + name + (info ? ' - ' + info : '')); };
const card = (id, x, extra) => Object.assign({ id, immutable: false, enabled: true, execution_mode: 'observe', pos: { x, y: 60 } }, extra);
const doc = {
  schema: 'workflow-board/v1', board: BOARD, version: 1, execution_mode: 'observe', saved_at: '2026-10-14T00:00:00Z', integrity_hash: null,
  cards: [
    card('st-a', 40, { type: 'step', label: 'Start', ref: { kind: 'task' }, ports: { in: [], out: ['next'] } }),
    card('rs-plugin', 320, { type: 'resource', label: 'some-plugin', ref: { kind: 'plugin', name: 'some-plugin', harness: 'claude' }, ports: { in: ['in'], out: ['out'] } }),
  ],
  edges: [{ from: 'st-a.next', to: 'rs-plugin.in' }],
};
(async () => {
  const put = await fetch(`${BASE}/board/${BOARD}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(doc) });
  ok('resource board saves', put.status === 200 || put.status === 201, 'HTTP ' + put.status + ' ' + (put.ok ? '' : await put.text()));
  const launch = process.env.WB_CHROME ? { executablePath: process.env.WB_CHROME, headless: true } : { channel: 'chrome', headless: true };
  const browser = await chromium.launch(launch);
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  page.on('pageerror', e => errs.push('pageerror: ' + e));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.goto(`${BASE}/?board=${BOARD}`);
  await page.waitForSelector('.card[data-id="rs-plugin"]', { timeout: 20000 });
  const r = await page.evaluate(() => {
    const d = document.querySelector('.card[data-id="rs-plugin"]');
    return { type: d.dataset.type, icon: (d.querySelector('.hd i') || {}).textContent, grey: d.classList.contains('grey'),
             palette: [...document.querySelectorAll('#palList .pi span')].map(s => s.textContent).includes('resource') };
  });
  ok('resource card renders', r.type === 'resource' && r.icon === '\u25C7', JSON.stringify(r));
  ok('resource card is never greyed', r.grey === false);
  ok('resource is in the palette', r.palette === true);
  const unknown = await page.evaluate(() => {
    const W = window.wb, n = document.querySelectorAll('#cards > .card').length;
    W.board.cards.push({ id: 'zz-future', type: 'future-kind', label: 'future', immutable: false, enabled: true, execution_mode: 'observe',
                         ref: { kind: 'x' }, ports: { in: ['in'], out: ['out'] }, pos: { x: 600, y: 60 } });
    W.render();
    const d = document.querySelector('.card[data-id="zz-future"]');
    return { before: n, after: document.querySelectorAll('#cards > .card').length, icon: d && (d.querySelector('.hd i') || {}).textContent };
  });
  ok('unknown card type renders with the fallback icon', unknown.after === unknown.before + 1 && unknown.icon === '\u25C7', JSON.stringify(unknown));
  ok('no page or console errors', errs.length === 0, errs.join(' | '));
  await browser.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log('SCRIPT ERROR ' + e); process.exit(1); });
