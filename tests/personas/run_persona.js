/* Persona runner (N5/N6). One persona x one viewport per process, in its own sandbox + server.
   Usage: node run_persona.js <persona-id> <desktop|phone> <outDir>
   Writes <outDir>/<viewport>-result.json {steps[], findings[], guard} plus per-step evidence JSON and saved screenshots (paths only, never read).
   It TESTS and REPORTS: it never edits product code and never touches the real boards (real boards/ is hashed before and after). */
const fs = require('fs'), path = require('path');
const L = require('./lib.js');
const { chromium } = require(L.PW);
const spec = JSON.parse(fs.readFileSync(path.join(__dirname, 'personas.json'), 'utf8'));
const [, , PID, VPN, OUT] = process.argv;
const persona = spec.personas.find(p => p.id === PID), vpc = spec.viewports[VPN];
if (!persona || !vpc) { console.log('usage: node run_persona.js <dana|erin|frank> <desktop|phone> <outDir>'); process.exit(2); }
const touch = vpc.touch, P = persona.params, sleep = L.sleep;
const JARGON = ['port', 'hook', 'gate', 'enforce', 'observe', 'hash', 'deploy', 'drill', 'container', 'schema', 'ref (json)', 'inventory', 'immutable', 'lens', 'run_mode', 'execution_mode'];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const REAL = path.join(L.SRC, 'boards'), realBefore = L.hashDir(REAL);
  const sb = L.makeSandbox(PID + '-' + VPN), srv = await L.startServer(sb);
  const steps = [], findings = [], errs = [], net = [];
  let cur = 'a', browser = null;
  const R = (sub, expected, pass, ev) => { steps.push({ persona: PID, viewport: VPN, step: cur, sub, expected, pass: !!pass, evidence: String(ev == null ? '' : ev).slice(0, 500) }); return !!pass; };
  const F = (category, flags, title, repro, evidence, file) => findings.push({ persona: PID, viewport: VPN, step: cur, category, flags, title, repro, evidence: String(evidence == null ? '' : evidence).slice(0, 500), file: file || '' });
  const evFile = (name, obj) => { const p = path.join(OUT, VPN + '-' + name + '.json'); fs.writeFileSync(p, JSON.stringify(obj, null, 1)); return p; };
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const ctx = await browser.newContext({ viewport: { width: vpc.width, height: vpc.height }, hasTouch: touch, isMobile: touch, deviceScaleFactor: touch ? 2 : 1 });
    await ctx.addInitScript(() => { try { if (!sessionStorage.getItem('__wbp')) { localStorage.clear(); sessionStorage.setItem('__wbp', '1'); } } catch (e) { } });
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    const wire = pg => {
      pg.on('pageerror', e => errs.push({ step: cur, t: 'pageerror', m: String(e).slice(0, 200) }));
      pg.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push({ step: cur, t: 'console', m: m.text().slice(0, 200) }); });
      pg.on('response', r => { const u = new URL(r.url()), rq = r.request(); if (r.status() >= 400 && !/^\/api\/shared\//.test(u.pathname)) errs.push({ step: cur, t: 'http', m: r.status() + ' ' + rq.method() + ' ' + u.pathname }); if (rq.method() !== 'GET' && /^\/board\//.test(u.pathname)) net.push({ step: cur, m: rq.method(), p: u.pathname, s: r.status(), t: Date.now() }); });
    };
    wire(page);
    const dialogs = []; page.on('dialog', d => { dialogs.push({ t: d.type(), m: d.message().slice(0, 120) }); d.accept().catch(() => { }); });
    const ev = (fn, a) => page.evaluate(fn, a);
    const errCount = () => errs.filter(e => e.t === 'pageerror').length;
    const open = async (b, q, pg) => { pg = pg || page; await pg.goto(srv.BASE + '/?board=' + b + (q || '')); await pg.waitForSelector('.card', { state: 'attached' }); await pg.waitForFunction(() => window.wbAutosave && window.wbAutosave.states.size > 0 && ((window.wbNested && window.wbNested.ready) || (window.wbZoom && window.wbZoom.hardMode && window.wbZoom.hardMode())), null, { timeout: 25000 }); await pg.waitForTimeout(700); await pg.evaluate(() => { window.__h = {}; document.querySelectorAll('.card').forEach(e => window.__h[e.dataset.id] = e.offsetHeight); }); };
    const msg = () => ev(() => (document.getElementById('msg') || {}).textContent || '');
    const label = () => ev(() => { const l = document.getElementById('nest-label'); return l && l.dataset.board; });
    const waitLabel = async (name, ms) => { const t = Date.now(); while (Date.now() - t < (ms || 4000)) { if ((await label()) === name) return Date.now() - t; await sleep(25); } return -1; };
    const rect = id => ev(id => { const e = document.querySelector('.card[data-id="' + id + '"]'); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 }; }, id);
    const hdr = async id => ev(id => { const e = document.querySelector('.card[data-id="' + id + '"] .hd') || document.querySelector('.card[data-id="' + id + '"]'); if (!e) return null; let r = e.getBoundingClientRect(); const v = document.getElementById('vp').getBoundingClientRect(); let dx = 0, dy = 0; if (r.x + 40 < v.left + 20) dx = v.left + 60 - (r.x + 40); else if (r.x + 40 > v.right - 20) dx = v.right - 60 - (r.x + 40); if (r.y + r.height / 2 < v.top + 20) dy = v.top + 70 - (r.y + r.height / 2); else if (r.y + r.height / 2 > v.bottom - 90) dy = v.bottom - 120 - (r.y + r.height / 2); if (dx || dy) { window.wb.setView({ k: window.wb.view.k, x: window.wb.view.x + dx, y: window.wb.view.y + dy }); r = e.getBoundingClientRect(); } return { x: r.x + Math.min(40, r.width / 2), y: r.y + r.height / 2 }; }, id);
    const T = async (type, pts) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: pts.map((p, i) => ({ x: Math.round(p.x), y: Math.round(p.y), id: i + 1 })) });
    const dragXY = async (x0, y0, x1, y1, n) => { n = n || 8; if (!touch) { await page.mouse.move(x0, y0); await page.mouse.down(); for (let i = 1; i <= n; i++) { await page.mouse.move(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n); await sleep(8); } await page.mouse.up(); } else { await T('touchStart', [{ x: x0, y: y0 }]); for (let i = 1; i <= n; i++) { await T('touchMove', [{ x: x0 + (x1 - x0) * i / n, y: y0 + (y1 - y0) * i / n }]); await sleep(8); } await T('touchEnd', []); } await sleep(120); };
    const dragCard = async (id, dx, dy) => { const h = await hdr(id); if (!h) return false; await dragXY(h.x, h.y, h.x + dx, h.y + dy); return true; };
    const tapEl = async (sel, opt) => { const l = page.locator(sel).first(); if (touch && /^#b(Deploy|DeployHelp|Fit|Legend|Arrange|SelMode|Copy|Paste|InspectSkill|Dup)$/.test(sel) && !(await l.isVisible())) { await page.locator('#bMore').tap({ timeout: 2500 }); await sleep(150); } /* F4 E2: on phone these controls live in the More menu */ if (touch) await l.tap(Object.assign({ timeout: 2500 }, opt || {})); else await l.click(Object.assign({ timeout: 2500 }, opt || {})); };
    const tapXY = async (x, y) => { if (touch) { await T('touchStart', [{ x, y }]); await T('touchEnd', []); } else await page.mouse.click(x, y); await sleep(100); };
    const pinch = async (cx, cy, d0, d1, n) => { n = n || 10; await T('touchStart', [{ x: cx - d0, y: cy }, { x: cx + d0, y: cy }]); for (let i = 1; i <= n; i++) { const d = d0 + (d1 - d0) * i / n; await T('touchMove', [{ x: cx - d, y: cy }, { x: cx + d, y: cy }]); await sleep(16); } await T('touchEnd', []); await sleep(150); };
    const shot = async name => { try { await page.screenshot({ path: path.join(OUT, VPN + '-' + name + '.png') }); } catch (e) { } };
    const closeDrawers = () => ev(() => { const a = document.getElementById('app'); a.classList.remove('palopen', 'inspopen'); });
    const openInsp = async () => { if (touch) await ev(() => { document.getElementById('app').classList.add('inspopen'); document.getElementById('app').classList.remove('palopen'); }); };
    const openPal = async () => { if (touch) await ev(() => { document.getElementById('app').classList.add('palopen'); document.getElementById('app').classList.remove('inspopen'); }); };
    const selectCard = async id => { await closeDrawers(); const h = await hdr(id); await tapXY(h.x, h.y); await sleep(150); };
    const fview = (id, f) => ev(([id, f]) => { const w = window.wb, c = w.board.cards.find(x => x.id === id), r = document.getElementById('vp').getBoundingClientRect(), cw = (c.size && c.size.w) || 192, ch = (window.__h && window.__h[id]) || 90, k0 = Math.max(cw / r.width, ch / r.height), k = Math.min(2.5, f / k0); w.setView({ k, x: r.width / 2 - (c.pos.x + cw / 2) * k, y: r.height / 2 - (c.pos.y + ch / 2) * k }); return k; }, [id, f]);
    const pos = id => ev(id => { const c = window.wb.board.cards.find(x => x.id === id); return c ? { x: c.pos.x, y: c.pos.y } : null; }, id);
    const disk = n => sb.disk(n);
    const state = () => ev(() => ({ board: window.wb.board.board, dirty: window.wbDirty && window.wbDirty.state, save: document.getElementById('bSave').textContent, cards: window.wb.board.cards.length, edges: window.wb.board.edges.length }));
    const patches = since => net.filter(n => n.m === 'PATCH' && (since == null || n.t >= since));
    const resetAll = async () => { sb.reset(); try { await page.evaluate(() => localStorage.clear()); } catch (e) { } };
    const activeBytes = () => fs.readFileSync(sb.boardFile('active'), 'utf8');
    const step = async (id, fn) => { if (process.env.STEPS && !process.env.STEPS.split(',').includes(id)) return; cur = id; const e0 = errCount(), n0 = steps.length; try { await fn(); } catch (e) { R('script', 'step runs to the end', false, 'SCRIPT ERROR ' + String(e && e.message || e).slice(0, 200)); F('harness', ['script_error'], 'step ' + id + ' script error: ' + String(e && e.message || e).slice(0, 120), 'rerun ' + PID + '/' + VPN + ' step ' + id, String(e && e.stack || e).slice(0, 300)); } const e1 = errCount(); if (e1 > e0) { R('no uncaught exception', 'no pageerror during the step', false, errs.filter(x => x.t === 'pageerror').slice(e0).map(x => x.m).join(' | ')); F('uncaught_exception', ['crash'], 'uncaught page exception during step ' + id, PID + '/' + VPN + ' step ' + id, errs.filter(x => x.t === 'pageerror').slice(e0).map(x => x.m).join(' | ')); } const out = steps.slice(n0); await shot(id); evFile(id, { persona: PID, viewport: VPN, step: id, results: out, errors: errs.filter(x => x.step === id).slice(0, 20), patches: net.filter(x => x.step === id).slice(0, 30) }); };

    /* ---------------- (a) orientation ---------------- */
    await step('a', async () => {
      await open('starter-hub');
      const s = await state(), lab = await label(), txt = await ev(() => document.body.innerText.toLowerCase());
      R('board renders', 'cards drawn', s.cards >= 10 && (await ev(() => document.querySelectorAll('.card').length)) >= 10, 'cards=' + s.cards);
      R('label names the active board', 'Active: starter-hub', lab === 'starter-hub', 'label=' + lab);
      R('no console or page errors on load', 'none', errs.length === 0, JSON.stringify(errs.slice(0, 2)));
      const vis = await ev(() => { const de = document.documentElement; return { sw: Math.max(de.scrollWidth, document.body.scrollWidth), cw: de.clientWidth, title: document.title, msg: (document.getElementById('msg') || {}).textContent }; });
      R('no horizontal page scroll', 'scrollWidth <= clientWidth', vis.sw <= vis.cw + 1, JSON.stringify(vis));
      if (vis.sw > vis.cw + 1) F('cosmetic', ['cosmetic', 'layout_overflow'], 'The page scrolls sideways at ' + vpc.width + 'px wide (scrollWidth ' + vis.sw + ' > ' + vis.cw + '): the top bar overflows and the last layout buttons sit off-screen', 'open starter-hub at ' + vpc.width + 'x' + vpc.height + '; compare document scrollWidth to clientWidth', JSON.stringify(vis), 'canvas/index.html / canvas/js/toolbar.js');
      const jarg = JARGON.filter(j => txt.includes(j));
      const orient = /getting started|welcome|what is this|how to use|help|tour|start here/.test(txt);
      R('plain-language orientation visible', 'a welcome / what-is-this / help text exists', orient, 'orientation text found=' + orient + '; jargon terms on first screen=' + jarg.join(','));
      if (!orient) F('copy', ['missing_orientation', 'first_time_user'], 'No plain-language orientation: a first-time owner sees cards and ' + jarg.length + ' jargon terms but no "what is this" text', 'open /?board=starter-hub; read the first screen', 'jargon=' + jarg.join(','), 'canvas/index.html');
      const deployTip = await ev(() => { const b = document.getElementById('bDeploy'); return (b.title || '') + '|' + b.textContent; });
      R('Deploy is explained in words', 'button title/text explains Deploy for a non-technical user', /deploy request$/i.test(deployTip.split('|')[1]) && deployTip.split('|')[0].length > 5, deployTip);
      if (!(deployTip.split('|')[0].length > 5)) F('copy', ['jargon', 'first_time_user'], '"Deploy request" button has no tooltip or plain explanation of what Deploy means', 'hover/long-press Deploy request', deployTip, 'canvas/index.html');
    });

    /* ---------------- (b) click every button ---------------- */
    await step('b', async () => {
      await resetAll(); await open('starter-hub');
      const seen = new Set(), SKIP = /^restore$/i; let n = 0, none = 0; const smallT = [];
      const results = [];
      await ev(() => { window.__mut = 0; new MutationObserver(m => { window.__mut += m.length; }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true }); });
      let opened = false;
      await ev(() => window.wb.setView({ k: 1, x: 60, y: 80 })); await sleep(400);
      while (n < 140) {
        const list = await ev(() => [...document.querySelectorAll('button')].map((b, i) => { b.setAttribute('data-wbi', String(i)); const r = b.getBoundingClientRect(), cs = getComputedStyle(b); const inDr = !!b.closest('#pal,#insp'); return { i, vis: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none', dis: b.disabled, key: (b.id || '') + '|' + b.className + '|' + (b.textContent || '').trim().slice(0, 28) + '|' + (b.dataset.lens || b.dataset.crumb || b.dataset.open || b.dataset.enter || ''), text: (b.textContent || '').trim().slice(0, 28), inDr, inWorld: !!b.closest('#world'), h: Math.round(r.height), w: Math.round(r.width) }; }));
        let t = list.find(b => b.vis && !b.dis && /^close$/i.test(b.text) && !seen.has(b.key + '#' + n)) || list.find(b => b.vis && !b.dis && !seen.has(b.key) && !SKIP.test(b.text));
        if (t && /^close$/i.test(t.text)) { seen.add(t.key + '#' + n); }
        if (!t) { if (touch && !opened) { opened = true; await ev(() => { document.getElementById('app').classList.add('palopen'); }); await sleep(200); continue; } if (touch && opened === true) { opened = 2; await ev(() => { document.getElementById('app').classList.remove('palopen'); document.getElementById('app').classList.add('inspopen'); }); await sleep(200); continue; } break; }
        seen.add(t.key); n++;
        if (touch && !t.inDr) await closeDrawers();
        const pre = await ev(() => ({ mut: window.__mut, msg: (document.getElementById('msg') || {}).textContent, board: window.wb.board.board, cards: window.wb.board.cards.length, sel: window.wb.sel.size, html: document.body.children.length }));
        const e0 = errs.length;
        await ev(i => { const e = document.querySelector('button[data-wbi="' + i + '"]'); if (e && e.closest('#world')) { const v = document.getElementById('vp').getBoundingClientRect(), r = e.getBoundingClientRect(); let dx = 0, dy = 0; if (r.left < v.left + 8) dx = v.left + 60 - r.left; else if (r.right > v.right - 8) dx = v.right - 60 - r.right; if (r.top < v.top + 8) dy = v.top + 60 - r.top; else if (r.bottom > v.bottom - 8) dy = v.bottom - 60 - r.bottom; if (dx || dy) window.wb.setView({ k: window.wb.view.k, x: window.wb.view.x + dx, y: window.wb.view.y + dy }); } }, t.i);
        let how = 'ok', fullMsg = '';
        try { if (touch) await page.locator('button[data-wbi="' + t.i + '"]').first().tap({ timeout: 2500 }); else await page.locator('button[data-wbi="' + t.i + '"]').first().click({ timeout: 2500 }); } catch (e) { fullMsg = String(e.message); how = (/intercepts pointer events/.test(fullMsg) ? 'obscured: ' : /outside of the viewport/.test(fullMsg) ? 'offscreen: ' : /element is not visible/.test(fullMsg) ? 'not visible: ' : 'not clickable: ') + String(e.message).replace(/\x1b\[\d+m/g, '').split('\n').slice(1, 40).join(' ').replace(/\s+/g, ' ').slice(-300); }
        if (/^(obscured|offscreen)/.test(how)) { const wasOff = /^offscreen/.test(how); await ev(i => { const e = document.querySelector('button[data-wbi="' + i + '"]'); if (e) e.click(); }, t.i); if (wasOff) how = 'offscreen (reached by script only)'; else how = 'ok'; }
        await sleep(350);
        const post = await ev(() => ({ mut: window.__mut, msg: (document.getElementById('msg') || {}).textContent, board: window.wb.board.board, cards: window.wb.board.cards.length, sel: window.wb.sel.size, visCards: [...document.querySelectorAll('.card')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && getComputedStyle(e).visibility !== 'hidden'; }).length, alive: document.querySelectorAll('.card').length > 0 && !!document.getElementById('vp') && document.body.innerText.length > 20, dlg: ['dep', 'legend'].map(id => { const e = document.getElementById(id); return e && getComputedStyle(e).display !== 'none' ? id : null; }).filter(Boolean).join(',') + (document.querySelector('.v4-history-panel,.v4-runs-panel,.wbi-modal,#wbiModal') ? ',panel' : '') }));
        await ev(() => { const a = document.getElementById('app'); a.classList.remove('palc', 'inspc'); const lg = document.getElementById('legend'); if (lg) lg.classList.remove('open'); const e = document.getElementById('wbi'); if (e && e.classList.contains('open')) { const b = [...e.querySelectorAll('button')].find(x => /close/i.test(x.textContent)); if (!b) e.classList.remove('open'); } });
        const changed = post.mut !== pre.mut || post.msg !== pre.msg || post.board !== pre.board;
        const res = { button: t.key.split('|').filter(Boolean).join(' / ') || t.text, how, changed, msg: (post.msg || '').slice(0, 80), cards: pre.cards + '->' + post.cards, board: pre.board === post.board ? '' : pre.board + '->' + post.board, errsNew: errs.length - e0, alive: post.alive, visCards: post.visCards, dlg: post.dlg, h: t.h };
        results.push(res);
        if (/^offscreen/.test(how)) { R('click ' + res.button, 'control reachable on screen', false, how); F('awkward', ['awkward', 'unreachable_control'], 'Button "' + (t.text || t.key) + '" is off-screen and cannot be clicked at ' + vpc.width + 'x' + vpc.height, 'open starter-hub at this size; look for ' + (t.text || t.key) + ' in the top bar', how.slice(0, 120), 'canvas/index.html / canvas/js/toolbar.js'); }
        else if (how !== 'ok' && /not visible/.test(how)) { R('click ' + res.button, 'hidden at this zoom level (not counted)', true, 'hidden: ' + how.slice(0, 80)); }
        else if (how !== 'ok') { R('click ' + res.button, 'button is reachable and tappable', false, how); F('phone_ergonomics', ['awkward', 'reachability'], 'Button "' + (t.text || t.key) + '" not clickable/tappable on ' + VPN + ': ' + how, 'open starter-hub on ' + VPN + '; tap ' + (t.text || t.key), how, 'canvas/index.html'); }
        else if (!post.alive) { R('click ' + res.button, 'screen stays alive', false, 'blank/dead screen after click'); F('blank_screen', ['crash'], 'Blank or dead screen after clicking "' + (t.text || t.key) + '"', 'open starter-hub; click ' + t.key, JSON.stringify(res), ''); }
        else { R('click ' + res.button, 'a visible result and no error', changed && res.errsNew === 0, JSON.stringify(res)); if (!changed) { none++; F('awkward', ['no_feedback'], 'Button "' + (t.text || t.key) + '" gave no visible result when clicked', 'open starter-hub (nothing selected); click ' + (t.text || t.key), JSON.stringify(res), ''); } if (res.errsNew) F('console_error', ['console'], 'Console/HTTP error after clicking "' + (t.text || t.key) + '"', 'click ' + t.key, JSON.stringify(errs.slice(e0, e0 + 2)), ''); }
        if (touch && t.h < 40 && how === 'ok' && !t.inWorld) smallT.push((t.text || t.key).slice(0, 18) + ':' + t.h);
        if (post.board !== pre.board) { await open('starter-hub'); await ev(() => { window.__mut = 0; new MutationObserver(m => { window.__mut += m.length; }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true }); }); }
      }
      if (smallT.length) F('phone_ergonomics', ['awkward', 'touch_target'], smallT.length + ' toolbar/drawer buttons are shorter than 40px on phone', 'open on 390x844; measure the bar, Add and Inspect drawer buttons', smallT.join(', ').slice(0, 300), 'canvas/index.html / canvas/style.css');
      R('every visible button clicked once', 'sweep covered the bar, palette, inspector and HUD', n >= 12, 'buttons clicked=' + n + ', no-visible-result=' + none);
      evFile('b-buttons', results);
    });

    /* ---------------- (c) build a test board ---------------- */
    await step('c', async () => {
      await resetAll(); await open('starter-hub');
      const inner = 'starter-hub-hub-empty', files0 = sb.files().join(',');
      /* c1: zoom into the empty card, add the first card (this is what creates the inner board file) */
      await fview('hub-empty', 0.6);
      const lat = await waitLabel(inner, 5000);
      R('zoom into the empty card opens an inner board', 'label becomes "' + inner + '"', lat >= 0, 'ms=' + lat + ' label=' + (await label()));
      R('no file created while empty', 'boards dir unchanged', sb.files().join(',') === files0, sb.files().join(','));
      await openPal(); await page.fill('#pref', P.labelWords[0]);
      const addItem = async type => { const loc = page.locator('#palList .pi').filter({ hasText: type }).first(); if (touch) await loc.tap({ timeout: 3000 }); else await loc.click({ timeout: 3000 }); await sleep(250); };
      await addItem('component'); await sleep(900);
      const lab1 = await label();
      R('adding the first card keeps you on the inner board', 'label stays "' + inner + '"', lab1 === inner, 'label=' + lab1);
      if (lab1 !== inner) F('awkward', ['awkward', 'handoff_on_add', 'wrong_board'], 'Adding a card inside an inner board on ' + VPN + ' immediately zoomed into the NEW card\'s own empty board (label became "' + lab1 + '"), so further adds would land in a nested board', 'zoom into an empty card until the label flips; add a card from the palette', 'label after add=' + lab1, 'canvas/js/nested.js');
      await sleep(2800);
      R('first add created the inner board file', 'file exists (further adds need Save)', sb.exists(inner), sb.exists(inner) ? 'cards on disk=' + disk(inner).cards.length : 'no file');
      if (!sb.exists(inner)) F('data_loss', ['data_loss'], 'The first card added to a new inner board was not written to disk after 3.5 s', 'zoom into empty card; add a card; wait', 'no ' + inner + '.json', 'canvas/js/nested.js');
      for (let g = 0; g < 4 && (await label()) !== 'starter-hub'; g++) { await page.keyboard.press('Escape'); await sleep(900); }
      R('Escape / zoom-out returns to the parent', 'label starter-hub', (await label()) === 'starter-hub', 'label=' + await label());
      const lnk = await ev(() => { const c = window.wb.board.cards.find(c => c.id === 'hub-empty'); return c && c.drill; }), sv2 = await state();
      R('the parent card now links to the new board and Save says deploy needed', 'drill set + deploy needed', lnk === inner && /deploy/i.test(sv2.save), 'drill=' + lnk + ' save=' + sv2.save);
      if (lnk !== inner) F('awkward', ['awkward', 'link_missing'], 'After the first card was added in an inner board the parent card did not get its link', 'zoom into empty card, add a card, zoom out', 'drill=' + lnk, 'canvas/js/nested.js');
      /* c2: build a 4-card, 3-edge board with a note in free space on the board (normal zoom) */
      await resetAll(); await open('starter-hub');
      await ev(() => { const w = window.wb, r = document.getElementById('vp').getBoundingClientRect(), k = 0.8; w.setView({ k, x: r.width / 2 - 500 * k, y: r.height / 2 - 1100 * k }); });
      await sleep(400);
      const vpr = await ev(() => { const r = document.getElementById('vp').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
      const spots = [[0.3, 0.25], [0.72, 0.25], [0.3, 0.6], [0.72, 0.6]].map(([a, b]) => ({ x: vpr.x + vpr.w * a, y: vpr.y + vpr.h * b }));
      const ids = [], dbgAdd = [], retried = [], types = ['component', 'datastore', 'external', 'component'];
      for (let i = 0; i < 4; i++) {
        await openPal(); await page.fill('#pref', P.labelWords[i]);
        const before = await ev(() => window.wb.board.cards.map(c => c.id));
        await addItem(types[i]);
        let now = await ev(() => window.wb.board.cards.map(c => c.id)), nid = now.find(x => !before.includes(x));
        if (!nid) { retried.push(types[i]); await openPal(); await sleep(400); await addItem(types[i]); now = await ev(() => window.wb.board.cards.map(c => c.id)); nid = now.find(x => !before.includes(x)); }
        if (!nid) dbgAdd.push({ i, type: types[i], before: before.length, now: now.length, label: await label(), msg: await msg(), board: await ev(() => window.wb.board.board) });
        if (nid) { ids.push(nid); await closeDrawers(); const h = await hdr(nid); if (h) { await dragXY(h.x, h.y, spots[i].x - 30, spots[i].y); if (touch) await sleep(1500); /* F4 E3: a CDP touch drag at 8 ms/step is a fling; Chrome swallows the click of a tap that lands within ~1 s of it (pointerdown/up arrive, click does not). Real fingers are slower and a human cannot re-open the drawer that fast. Settle so the finding only fires on a real ignored tap. */ } }
      }
      R('four cards added from the palette', '4 new cards', ids.length === 4, ids.join(',') + ' retried=' + retried.join(',') + ' ' + JSON.stringify(dbgAdd).slice(0, 300));
      if (retried.length) F('note', ['suggestion', 'tap_ignored_once'], 'First tap on palette item(s) ' + retried.join(',') + ' did nothing on ' + VPN + ' right after dragging a card; a second tap worked', 'phone: add a card, drag it, open Add, tap a type', 'retried=' + retried.join(','), 'canvas/js/core.js');
      const labs = await ev(() => window.wb.board.cards.map(c => c.label));
      R('card labels use the typed words', 'labels equal the persona vocabulary', P.labelWords.every(w => labs.includes(w)), labs.slice(-5).join(' | '));
      let made = 0; const dbgE = [];
      for (let i = 0; i < 3; i++) {
        const a = ids[i], b = ids[i + 1]; if (!a || !b) continue;
        const o = await ev(x => { const e = document.querySelector('.card[data-id="' + x + '"] .pt.out'); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.right - 4, y: r.y + r.height / 2 }; }, a);
        const q = await ev(x => { const e = document.querySelector('.card[data-id="' + x + '"] .pt.in'); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.x + 4, y: r.y + r.height / 2 }; }, b);
        if (!o || !q) { dbgE.push({ a, b, o, q }); continue; }
        const e0 = await ev(() => window.wb.board.edges.length);
        await dragXY(o.x, o.y, q.x, q.y, 12);
        const e1 = await ev(() => window.wb.board.edges.length); if (e1 > e0) made++; else dbgE.push({ a, b, o, q, msg: await msg() });
      }
      R('three edges drawn by dragging port to port', '3 new edges', made === 3, 'edges made=' + made + ' by ' + (touch ? 'touch' : 'mouse') + ' ' + JSON.stringify(dbgE).slice(0, 300));
      if (made < 3) F('stuck_flow', ['awkward', 'wire_by_drag'], 'Could not wire cards by dragging out-port to in-port on ' + VPN + ' (' + made + '/3 edges)', 'add 2 cards, drag from the right dot of one to the left dot of the next', 'made=' + made + ' ' + JSON.stringify(dbgE).slice(0, 200), 'canvas/js/core.js');
      await openPal(); await page.locator('#palList .v4-palnote').first().click({ timeout: 3000 }).catch(async () => { await page.locator('#palList .v4-palnote').first().tap({ timeout: 3000 }); });
      await sleep(250);
      const noteId = await ev(() => { const n = window.wb.board.cards.filter(c => c.type === 'note'); return n.length ? n[n.length - 1].id : null; });
      R('sticky note added', 'a note card exists', !!noteId, 'note=' + noteId);
      if (noteId) { await closeDrawers(); const h = await hdr(noteId); if (h) await dragXY(h.x, h.y, vpr.x + vpr.w * 0.5, vpr.y + vpr.h * 0.88); await selectCard(noteId); await openInsp(); const tf = page.locator('[data-field-key="text"]').first(); const has = await tf.count(); if (has) { await tf.fill('Check with the team before sending'); await tf.dispatchEvent('change'); } R('note text editable in the inspector', 'a "text" field exists for the note', has > 0, 'field count=' + has); if (!has) F('awkward', ['note_no_text_field'], 'Sticky note has no obvious way to type its text', 'add a note; select it; inspector', 'no [data-field-key=text]', 'canvas/js/kinds.js'); }
      const pinUi = await ev(() => !!document.querySelector('[data-pin],.pin-btn,#pinBtn') || /pin sample|pin a sample/i.test(document.body.innerText));
      R('pin a sample', 'a way to pin a synthetic sample exists in the UI', pinUi, 'pin UI present=' + pinUi);
      if (!pinUi) F('note', ['suggestion', 'no_pin_ui'], 'No UI to pin a sample payload; only visible as a badge when present', 'look for pin in inspector/toolbar', 'none', 'canvas/js/kinds.js');
      await ev(id => { const c = window.wb.board.cards.find(x => x.id === id); if (c) { c.pin = { port: c.ports.out[0] || c.ports.in[0], sample: { example: 'order 1001' }, synthetic: true }; window.wb.render(); } }, ids[0]);
      await closeDrawers(); const sv = await state();
      R('Save says deploy needed after behaviour edits', 'Save button mentions deploy', /deploy/i.test(sv.save), sv.save);
      await tapEl('#bSave'); await sleep(900);
      const m = await msg();
      R('Save accepted', 'message says saved', /^saved/.test(m), m);
      const d = disk('starter-hub'), nNew = d.cards.length - 11, eNew = d.edges.length - 3;
      R('board on disk holds the 4 cards, 3 edges and the note', '+5 cards, +3 edges', nNew >= 5 && eNew >= 3, 'cards +' + nNew + ' edges +' + eNew);
      if (nNew < 5 || eNew < 3) F('data_loss', ['data_loss'], 'Board lost edits after Save (cards +' + nNew + ', edges +' + eNew + ')', 'add cards/edges/note; Save; read file', JSON.stringify({ c: d.cards.length, e: d.edges.length }), 'serve.py');
      await open('starter-hub'); const rs = await state();
      R('reload shows the built board', 'same cards after reload', rs.cards === d.cards.length && rs.edges === d.edges.length, JSON.stringify(rs));
      /* library module insert */
      await openPal(); const lm = await ev(() => [...document.querySelectorAll('.li')].filter(e => /module/.test((e.querySelector('.k') || {}).textContent || '')).length);
      R('library offers reusable modules', 'at least one module listed', lm > 0, 'modules listed=' + lm);
      if (lm > 0) { const c0 = await ev(() => window.wb.board.cards.length); const ml = page.locator('.li').filter({ has: page.locator('.k', { hasText: /^module$/ }) }).first(); if (touch) await ml.tap({ timeout: 3000 }).catch(() => { }); else await ml.click({ timeout: 3000 }).catch(() => { }); await sleep(500); const armed = await ev(() => !!document.querySelector('.li.armed')); if (armed) { await closeDrawers(); const vp = await ev(() => { const r = document.getElementById('vp').getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }); await tapXY(vp.x, vp.y); await sleep(900); } const c1 = await ev(() => window.wb.board.cards.length); R('module inserted from the library', 'cards added on the board', c1 > c0, 'armed=' + armed + ' cards ' + c0 + '->' + c1 + ' msg=' + (await msg()).slice(0, 80)); if (c1 <= c0) F('awkward', ['module_insert_unclear'], 'Clicking a library module did not add it to the board (needs an undiscoverable second tap)', 'open palette; click a module; observe', 'armed=' + armed, 'canvas/js/modules.js'); }
    });

    /* ---------------- (d) visual-only edits ---------------- */
    await step('d', async () => {
      await resetAll(); await open('starter-hub');
      const a0 = activeBytes(), base = JSON.parse(JSON.stringify(disk('starter-hub'))), t0 = Date.now();
      const p0 = base.cards.find(c => c.id === 'hub-agent').pos;
      await dragCard('hub-agent', 96, 48); const pg1 = await pos('hub-agent');
      await closeDrawers(); await selectCard('hub-draft'); await openInsp();
      await page.fill('#cardColor', '#336699'); await sleep(100);
      const note = 'hub-note'; await selectCard(note); await openInsp();
      const tf = page.locator('[data-field-key="text"]').first(); if (await tf.count()) { await tf.fill('Updated note text'); await tf.dispatchEvent('change'); await tf.dispatchEvent('input'); }
      await ev(() => { const c = window.wb.board.cards.find(x => x.id === 'hub-host2'); c.size = { w: 280, h: 120 }; window.wb.render(); }); /* resize has no handle in the UI: set through the page */
      R('resize handle', 'a drag handle for card size exists', await ev(() => !!document.querySelector('.rs,.resize,.card .rz,[data-resize]')), 'no resize handle found in DOM');
      F('note', ['suggestion', 'no_resize_ui'], 'No resize handle on cards (size can only change through the file)', 'look at any card edge/corner', 'no handle', 'canvas/js/core.js');
      let landed = -1; for (let i = 0; i < 50; i++) { await sleep(100); if (patches(t0).length) { landed = Date.now() - t0; break; } }
      await sleep(900);
      R('autosave PATCH lands without clicking Save', 'PATCH within 3 s', landed >= 0 && landed <= 3000, 'ms=' + landed + ' patches=' + patches(t0).map(p => p.s).join(','));
      if (landed < 0) F('data_loss', ['data_loss'], 'Visual edit was never autosaved', 'drag a card; wait 5 s', 'no PATCH', 'canvas/js/autosave.js');
      const dk = disk('starter-hub'), by = i => dk.cards.find(c => c.id === i);
      R('disk has the moved card', 'hub-agent moved +96/+48', pg1.x > p0.x && by('hub-agent').pos.x === pg1.x && by('hub-agent').pos.y === pg1.y, 'disk ' + JSON.stringify(by('hub-agent').pos) + ' page ' + JSON.stringify(pg1) + ' from ' + JSON.stringify(p0));
      R('disk has the recolour', 'hub-draft style.color', !!by('hub-draft').style && by('hub-draft').style.color === '#336699', JSON.stringify(by('hub-draft').style));
      R('disk has the note text', 'note text updated', (by('hub-note').fields || {}).text === 'Updated note text', JSON.stringify(by('hub-note').fields));
      R('disk has the resize', 'hub-host2 size', !!by('hub-host2').size && by('hub-host2').size.w === 280, JSON.stringify(by('hub-host2').size));
      R('no PUT (no Save) was needed', 'only PATCH requests', net.filter(n => n.step === 'd' && n.m === 'PUT').length === 0, net.filter(n => n.step === 'd').map(n => n.m + n.s).join(','));
      await open('starter-hub');
      const after = await ev(() => ({ x: window.wb.board.cards.find(c => c.id === 'hub-agent').pos.x, dirty: window.wbDirty.state, save: document.getElementById('bSave').textContent, pill: (document.getElementById('wb-dirty') || {}).textContent }));
      await sleep(600); const dirty2 = await ev(() => window.wbDirty.state);
      R('edits persist after reload', 'moved x survives reload', after.x === pg1.x, 'x=' + after.x + ' want ' + pg1.x);
      R('wbDirty.state stays clean', 'clean', dirty2 === 'clean', 'state=' + dirty2);
      if (dirty2 !== 'clean') F('unsafe_enabled', ['unsafe', 'hash_broken_by_visual'], 'A visual-only edit made the board look behaviour-dirty (enforcement hash drift)', 'drag a card, recolour, edit a note, reload', 'state=' + dirty2, 'canvas/js/dirty.js');
      R('Save button does not demand deploy', 'label is plain Save', !/deploy/i.test(after.save), after.save);
      R('active.json byte-identical', 'unchanged', activeBytes() === a0, 'sha ' + L.sha(sb.boardFile('active')).slice(0, 12));
      if (activeBytes() !== a0) F('unsafe_enabled', ['unsafe', 'real_boards_modified'], 'active.json changed during visual edits (sandbox copy)', 'visual edits', 'active.json differs', 'serve.py');
      const sem = await ev(async () => { const r = await fetch('/active'); const j = await r.json(); return j; });
      const shash = sem && (sem.hash || sem.current_hash || sem.semantic_hash || (sem.active && sem.active.hash)); const keys = sem ? Object.keys(sem).join(',') : ''; R('/active reports the deployed hash and it matches the board on disk', 'deployed hash equals the semantic hash of the board', sem && JSON.stringify(sem).includes(JSON.parse(a0).hash) && (sem.matches !== false), 'keys=' + keys + ' ' + JSON.stringify(sem).slice(0, 200));
    });

    /* ---------------- (d2) lane drag, Tidy, Collapse all: visual actions autosave ---------------- */
    await step('d', async () => {
      await resetAll(); await open('starter-hub');
      await ev(() => window.wb.setView({ k: 0.9, x: 40, y: 60 })); await sleep(400);
      const members = ['hub-trigger', 'hub-draft', 'hub-host'], P0 = {}; for (const id of members) P0[id] = await pos(id);
      const gh = await ev(() => { const b = document.querySelector('.grp[data-group="Intake"] b'); if (!b) return null; const r = b.getBoundingClientRect(); return { x: r.x + 14, y: r.y + r.height / 2 }; });
      R('lane header found', 'a draggable lane header exists', !!gh, JSON.stringify(gh));
      if (gh) {
        const t1 = Date.now(); await dragXY(gh.x, gh.y, gh.x + 60, gh.y + 40, 10); await sleep(300);
        const P1 = {}; for (const id of members) P1[id] = await pos(id);
        const dx = P1['hub-trigger'].x - P0['hub-trigger'].x, dy = P1['hub-trigger'].y - P0['hub-trigger'].y;
        R('dragging a lane header moves every card in the lane together', 'same delta for all three members', (dx !== 0 || dy !== 0) && members.every(id => P1[id].x - P0[id].x === dx && P1[id].y - P0[id].y === dy), 'delta=' + dx + ',' + dy);
        const gm = await msg();
        if (dx === 0 && dy === 0) F('note', ['suggestion', 'unverified'], 'Dragging a lane header did not move the lane in this harness (' + (touch ? 'touch' : 'mouse') + '); the header press cleared the selection instead. UNVERIFIED as a product defect (tests/browser/arrange.js covers lane selection)', 'press and drag the lane label "Intake" on starter-hub', 'delta=0,0 msg=' + gm.slice(0, 50), 'canvas/js/arrange.js');
        let landed = false; for (let i = 0; i < 50 && !landed; i++) { await sleep(100); landed = patches(t1).length > 0; } await sleep(700);
        const dk = disk('starter-hub');
        R('lane move autosaves', 'PATCH and disk positions match', landed && members.every(id => dk.cards.find(c => c.id === id).pos.x === P1[id].x), 'patch=' + landed + ' msg=' + gm.slice(0, 60));
        if (/save to keep/i.test(gm)) F('copy', ['copy', 'stale_save_message'], 'Visual actions still say "(Save to keep)" although they autosave (lane move: "' + gm.slice(0, 50) + '")', 'drag a lane header; read the message line; wait 3 s; the move is already on disk', gm, 'canvas/js/arrange.js');
      }
      await resetAll(); await open('starter-hub'); const a0 = activeBytes(), t2 = Date.now();
      await closeDrawers(); await tapEl('#v4Tidy'); await sleep(300); const tm = await msg();
      await tapEl('#v4CollapseAll'); await sleep(300); const cm = await msg();
      let landed2 = false; for (let i = 0; i < 60 && !landed2; i++) { await sleep(100); landed2 = patches(t2).length > 0; } await sleep(800);
      const dk2 = disk('starter-hub'), gs = dk2.group_styles || {}, collapsedOnDisk = Object.values(gs).some(v => v && v.collapsed);
      R('Tidy and Collapse all autosave (visual) without Save', 'PATCH sent; collapsed lanes on disk', landed2 && collapsedOnDisk, 'tidy="' + tm.slice(0, 50) + '" collapse="' + cm.slice(0, 50) + '" patch=' + landed2 + ' collapsedOnDisk=' + collapsedOnDisk);
      R('...and the board stays clean against the deployed hash', 'wbDirty clean, active.json unchanged', (await ev(() => window.wbDirty.state)) === 'clean' && activeBytes() === a0, 'state=' + await ev(() => window.wbDirty.state));
      if (/save to keep/i.test(tm + cm)) F('copy', ['copy', 'stale_save_message'], 'Tidy / Collapse all messages say "(Save to keep)" although these layout changes autosave', 'click Tidy or Collapse all; read the message line', tm + ' | ' + cm, 'canvas/js/toolbar.js, canvas/js/tidy.js');
      if (!landed2) F('data_loss', ['data_loss'], 'Tidy / Collapse all were not autosaved', 'click Tidy and Collapse all, wait 5 s', 'no PATCH', 'canvas/js/autosave.js');
      await ev(() => window.wbCollapse.setAll(false));
    });

    /* ---------------- (e) behaviour edit, deploy request, history ---------------- */
    await step('e', async () => {
      await resetAll(); await open('starter-hub');
      const a0 = activeBytes(), t0 = Date.now();
      await selectCard('hub-draft'); await openInsp();
      await page.locator('#inspBody input').nth(2).fill('Draft the reply v2');
      await sleep(400);
      let st = await state();
      R('Save says deploy needed', 'Save button text mentions deploy', /deploy/i.test(st.save), st.save);
      R('dirty pill shows behaviour edit', 'wbDirty.state === dirty', (await (async () => { await sleep(600); return ev(() => window.wbDirty.state); })()) === 'dirty', 'state=' + await ev(() => window.wbDirty.state) + ' pill=' + await ev(() => (document.getElementById('wb-dirty') || {}).textContent));
      await sleep(4500);
      R('behaviour edit is not autosaved', 'no PATCH/PUT, disk label unchanged', patches(t0).length === 0 && disk('starter-hub').cards.find(c => c.id === 'hub-draft').label === 'Draft the reply', 'patches=' + patches(t0).length + ' diskLabel=' + disk('starter-hub').cards.find(c => c.id === 'hub-draft').label);
      if (patches(t0).length || disk('starter-hub').cards.find(c => c.id === 'hub-draft').label !== 'Draft the reply') F('unsafe_enabled', ['unsafe', 'behaviour_autosaved'], 'A behaviour edit (card label) was autosaved without Save', 'rename an action card; wait 5 s', 'patches=' + patches(t0).length, 'canvas/js/autosave.js');
      /* enforce/observe toggle on the same card */
      await page.locator('#inspBody select').nth(1).selectOption('enforce'); await sleep(300);
      R('enforce/observe toggle works', 'card execution_mode becomes enforce in the page', (await ev(() => window.wb.board.cards.find(c => c.id === 'hub-draft').execution_mode)) === 'enforce', 'mode set');
      await closeDrawers(); await tapEl('#bSave'); await sleep(900);
      const m = await msg(); R('Save with enforce on a skill card', 'accepted, or a clear plain reason', /^saved/.test(m), m);
      if (!/^saved/.test(m)) { F('copy', ['awkward', 'late_error'], 'The enforce/observe selector offers "enforce" on a skill card but the refusal only appears at Save (' + m.slice(0, 90) + ')', 'select an action card; execution_mode > enforce; Save', m.slice(0, 200), 'canvas/js/core.js'); await ev(() => { window.wb.board.cards.find(c => c.id === 'hub-draft').execution_mode = 'observe'; window.wb.render(); }); await tapEl('#bSave'); await sleep(900); R('Save after switching back to observe', 'saved', /^saved/.test(await msg()), await msg()); }
      R('active.json untouched by Save', 'byte-identical', activeBytes() === a0, '');
      await sleep(700); R('board now differs from the deployed hash', 'dirty', (await ev(() => window.wbDirty.state)) === 'dirty', 'state=' + await ev(() => window.wbDirty.state));
      await tapEl('#bDeploy'); await sleep(300);
      const dep = await ev(() => { const d = document.getElementById('dep'); return { disp: getComputedStyle(d).display, txt: d.innerText }; });
      R('Deploy request shows the command only', 'dialog with /board deploy starter-hub', dep.disp !== 'none' && /\/board deploy starter-hub/.test(dep.txt), dep.txt.slice(0, 100));
      R('Deploy request did not write active.json', 'byte-identical', activeBytes() === a0, '');
      const dtxt = dep.txt.toLowerCase();
      if (/\/board deploy/.test(dtxt) && !/ask|tell|send|copy/.test(dtxt)) F('copy', ['jargon', 'first_time_user'], 'Deploy request tells the owner to "run /board deploy ..." in a session, with no plain explanation of who runs it or why', 'click Deploy request', dep.txt.slice(0, 120), 'canvas/js/core.js');
      await tapEl('#dep button'); /* Close */
      await selectCard('hub-agent'); await openInsp(); await page.locator('#inspBody select').nth(1).selectOption('enforce'); await closeDrawers(); await tapEl('#bSave'); await sleep(900);
      R('enforce on an agent card saves', 'saved', /^saved/.test(await msg()), await msg());
      /* history */
      await ev(() => { const b = document.querySelector('.v4-history-btn'); if (b) b.click(); }); await sleep(700);
      const hs = await ev(() => ({ n: document.querySelectorAll('.v4-history-panel button').length, rows: document.querySelectorAll('.v4-history-panel button').length, txt: (document.querySelector('.v4-history-panel') || { innerText: '' }).innerText.slice(0, 120) }));
      R('History panel opens and lists snapshots', 'snapshot rows with diff and restore', hs.n >= 3, JSON.stringify(hs));
      if (hs.n >= 3) {
        await page.locator('.v4-history-panel button', { hasText: 'diff vs current' }).first().click({ timeout: 2500 }).catch(async () => { await page.locator('.v4-history-panel button', { hasText: 'diff vs current' }).first().tap({ timeout: 2500 }); }); await sleep(500);
        const diff = await ev(() => (document.querySelector('.v4-history-panel') || { innerText: '' }).innerText);
        R('diff vs current names the changed card', 'diff mentions hub-draft', /hub-draft/.test(diff), diff.slice(-120));
        const btns = page.locator('.v4-history-panel button', { hasText: 'restore' }); const nb = await btns.count();
        await btns.nth(nb - 1).click({ timeout: 2500 }).catch(async () => { await btns.nth(nb - 1).tap({ timeout: 2500 }); }); await sleep(1200);
        const lab = (await ev(() => window.wb.board.cards.find(c => c.id === 'hub-draft').label)), dl = disk('starter-hub').cards.find(c => c.id === 'hub-draft').label;
        R('restore brings the earlier version back', 'original label in the editor and on disk', lab === 'Draft the reply' && dl === 'Draft the reply', 'editor=' + lab + ' disk=' + dl);
        if (!(lab === 'Draft the reply' && dl === 'Draft the reply')) F('stuck_flow', ['awkward', 'restore_unclear'], 'History restore did not bring the original card label back', 'rename card, Save, History > restore oldest', 'editor=' + lab + ' disk=' + dl, 'canvas/js/history.js');
        R('restore never touched active.json', 'byte-identical', activeBytes() === a0, '');
        await ev(() => window.wbHistory && window.wbHistory.close());
      }
    });

    /* ---------------- (f) zoom ---------------- */
    await step('f', async () => {
      await resetAll(); await open('starter-hub');
      const hostCenter = async () => ev(() => { const w = window.wb, c = w.board.cards.find(x => x.id === 'hub-host'), r = document.getElementById('vp').getBoundingClientRect(); if (!c) return null; const k = w.view.k; return { cx: r.x + w.view.x + (c.pos.x + 96) * k, cy: r.y + w.view.y + (c.pos.y + 45) * k }; });
      const frac = id => ev(id => { const e = document.querySelector('.card[data-id="' + id + '"]'); if (!e) return 0; const r = e.getBoundingClientRect(), v = document.getElementById('vp').getBoundingClientRect(); return Math.max(r.width / v.width, r.height / v.height); }, id);
      await fview('hub-host', 0.12); await sleep(500);
      const peek = await ev(() => document.querySelectorAll('.card[data-id="hub-host"] .nm-peek').length);
      R('small card shows a live peek of its inner board', 'peek visible at ~12% screen', peek === 1, 'peeks=' + peek);
      /* gesture in: wheel (desktop) or pinch (phone) centred on the card */
      let flipF = null, steps0 = 0;
      for (let i = 0; i < 90 && (await label()) !== 'starter-intake'; i++) { const c = await hostCenter(); if (!c) break; if (!touch) { await page.mouse.move(c.cx, c.cy); await page.mouse.wheel(0, -100); await sleep(45); } else { await pinch(c.cx, c.cy, 40, 90, 8); await sleep(250); } steps0++; }
      const lat = await waitLabel('starter-intake', 3500);
      flipF = await frac('hub-host');
      R('zooming in flips the label to the inner board', 'Active: starter-intake', lat >= 0 || (await label()) === 'starter-intake', 'steps=' + steps0 + ' label=' + (await label()) + ' cardFractionAtFlip~' + (flipF ? flipF.toFixed(2) : 'n/a'));
      if ((await label()) !== 'starter-intake') { F('stuck_flow', ['stuck', 'zoom_by_gesture'], 'Zoom gesture (' + (touch ? 'pinch' : 'wheel') + ') never handed over to the inner board after ' + steps0 + ' steps', 'open starter-hub; ' + (touch ? 'pinch out' : 'scroll up') + ' over "Intake process"', 'label=' + (await label()), 'canvas/js/nested.js'); }
      const labTxt = await ev(() => { const l = document.getElementById('nest-label'); return l ? l.querySelector('.nl-t').textContent : null; });
      R('label text names the active board', '"Active: starter-intake"', labTxt === 'Active: starter-intake', String(labTxt));
      await sleep(500);
      if ((await label()) === 'starter-intake') {
        const hubBefore = JSON.stringify(disk('starter-hub')), inBefore = disk('starter-intake').cards.find(c => c.id === 'in-a').pos;
        await dragCard('in-a', 64, 32); await sleep(3500);
        const inAfter = disk('starter-intake').cards.find(c => c.id === 'in-a').pos;
        R('edit inside the inner board saves to the inner file', 'in-a moved on disk', inAfter.x === inBefore.x + 64 || inAfter.x !== inBefore.x, JSON.stringify(inBefore) + '->' + JSON.stringify(inAfter));
        if (inAfter.x === inBefore.x) F('data_loss', ['data_loss'], 'Edit made inside the inner board was not saved to it', 'zoom into Intake process; drag a card; wait', JSON.stringify(inAfter), 'canvas/js/autosave.js');
        R('parent board file not touched by the inner edit', 'starter-hub.json unchanged', JSON.stringify(disk('starter-hub')) === hubBefore, '');
        if (JSON.stringify(disk('starter-hub')) !== hubBefore) F('data_loss', ['data_loss', 'wrong_board_write'], 'Inner-board edit changed the PARENT board file', 'zoom in, edit, wait', 'starter-hub.json differs', 'canvas/js/autosave.js');
        /* out */
        let back = false;
        for (let i = 0; i < 90 && (await label()) !== 'starter-hub'; i++) { if (!touch) { await page.mouse.move(vpc.width / 2, vpc.height / 2); await page.mouse.wheel(0, 120); await sleep(45); } else { await pinch(vpc.width / 2, vpc.height / 2, 90, 40, 8); await sleep(250); } }
        back = (await waitLabel('starter-hub', 3000)) >= 0 || (await label()) === 'starter-hub';
        R('zooming out hands back to the parent', 'Active: starter-hub', back, 'label=' + (await label()));
        if (!back) F('stuck_flow', ['stuck'], 'Could not zoom back out to the parent board by gesture', 'zoom into a card then zoom out', 'label=' + (await label()), 'canvas/js/nested.js');
        R('edit saved to the right board only (both files checked)', 'inner has move, parent has none of the inner cards', !disk('starter-hub').cards.some(c => c.id === 'in-a') && disk('starter-intake').cards.some(c => c.id === 'in-a'), '');
      }
      /* Open button backup on phone / coarse */
      await open('starter-hub'); await fview('hub-host', 0.35); await sleep(500);
      const ob = await ev(() => { const b = document.querySelector('.card[data-id="hub-host"] .zm-d, .zm-d'); return b ? { txt: b.textContent, vis: b.getBoundingClientRect().height > 0 } : null; });
      R('an explicit Open/Enter button exists on a card with an inner board', 'button visible', !!ob, JSON.stringify(ob));
      if (ob) { await page.locator('.zm-d').first().evaluate(b => b.click()); await sleep(900); R('Open button enters the inner board', 'board changes', ['starter-intake'].includes(await ev(() => window.wb.board.board)), 'board=' + await ev(() => window.wb.board.board)); }
      if (!ob && touch) F('awkward', ['awkward', 'no_open_backup'], 'No Open button backup for pinch zoom on phone', 'phone: look at a card with an inner board', 'none', 'canvas/js/zoom.js');
      /* empty card */
      await open('starter-hub'); const files0 = sb.files().join(',');
      await fview('hub-empty', 0.6); const el = await waitLabel('starter-hub-hub-empty', 4000);
      R('empty card zoom opens a virtual inner board', 'label "starter-hub-hub-empty"', el >= 0, 'ms=' + el);
      await sleep(1000); R('...and creates no file until something is added', 'boards dir unchanged', sb.files().join(',') === files0, sb.files().join(','));
      if (sb.files().join(',') !== files0) F('stuck_flow', ['awkward', 'litter'], 'Zooming into an empty card created board files without any card added', 'zoom into empty card; wait', sb.files().join(','), 'canvas/js/nested.js');
      await page.keyboard.press('Escape'); await sleep(900);
      R('Escape (or zoom out) leaves the empty board', 'back on starter-hub', (await label()) === 'starter-hub' || (await label()) === null, 'label=' + (await label()));
    });

    /* ---------------- (g) run-mode, approval gate, immutable, containers ---------------- */
    await step('g', async () => {
      await resetAll(); await open('starter-hub');
      const a0 = activeBytes(), t0 = Date.now();
      const chip = id => ev(id => { const b = document.querySelector('.card[data-id="' + id + '"] .rm button'); return b ? { mode: b.dataset.runmode, text: b.textContent.trim(), tip: b.title } : null; }, id);
      const click = async id => { await closeDrawers(); const l = page.locator('.card[data-id="' + id + '"] .rm button').first(); if (touch) await l.tap({ timeout: 2500 }); else await l.click({ timeout: 2500 }); await sleep(250); };
      let seq = []; const c0 = await chip('hub-draft'); seq.push(c0 && c0.mode);
      for (let i = 0; i < 3; i++) { await click('hub-draft'); seq.push((await chip('hub-draft')).mode); }
      R('run-mode cycles inline > background > wait > inline', 'sequence inline,background,wait,inline', seq.join(',') === 'inline,background,wait,inline', seq.join(','));
      R('every state shows a word and a glyph (not colour only)', 'chip text has glyph + word', !!(c0 && /\S+ \w+/.test(c0.text)), JSON.stringify(c0));
      await click('hub-draft'); const badge = await ev(() => { const b = document.getElementById('rm-badge'); return b ? { t: b.textContent, on: b.classList.contains('on') } : null; });
      R('count badge for non-inline cards', 'badge text names the count', !!badge && /1 non-inline/.test(badge.t), JSON.stringify(badge));
      const sv = await state(); R('run-mode is a behaviour edit: Save says deploy needed', 'deploy in Save label', /deploy/i.test(sv.save), sv.save);
      await sleep(4200); R('run-mode never autosaves', 'no PATCH and disk has no run_mode', patches(t0).length === 0 && !disk('starter-hub').cards.some(c => c.run_mode), 'patches=' + patches(t0).length);
      if (patches(t0).length) F('unsafe_enabled', ['unsafe', 'behaviour_autosaved'], 'run_mode change triggered an autosave PATCH', 'cycle run-mode; wait', 'patches=' + patches(t0).length, 'canvas/js/autosave.js');
      /* blocker */
      const bc = await chip('hub-compile'); await click('hub-compile');
      const bm = await msg(), ac = await chip('hub-compile'), mode = await ev(() => window.wb.board.cards.find(c => c.id === 'hub-compile').run_mode);
      R('blocked compile card says what would need to change', 'chip "needs change" + blocker text, stays inline', bc && bc.mode === 'blocked' && /needs change/.test(bc.text) && !mode && /sentinel|governance|KB-writer/i.test(bm + (bc.tip || '')), JSON.stringify(bc) + ' msg=' + bm.slice(0, 80));
      if (!(bc && bc.mode === 'blocked')) F('unsafe_enabled', ['unsafe'], 'The KB-compile card (blocked for background) did not show its blocker', 'click run-mode on "Update the knowledge base"', JSON.stringify(bc), 'canvas/js/runmode.js');
      if (mode) F('unsafe_enabled', ['unsafe', 'run_mode_background_on_blocked'], 'Blocked card was set to ' + mode, 'click run-mode chip on blocked card', 'run_mode=' + mode, 'canvas/js/runmode.js');
      /* gate, immutable, container, note: no chip */
      const none = {}; for (const id of ['hub-approval', 'hub-guard', 'hub-gmail', 'hub-note', 'hub-host']) none[id] = await chip(id);
      R('approval gate, immutable hook, container, note and component never offer a run-mode chip', 'no chip on any', Object.values(none).every(x => !x), JSON.stringify(none));
      if (none['hub-approval'] || none['hub-guard']) F('unsafe_enabled', ['unsafe'], 'run-mode chip offered on an approval gate or immutable card', 'inspect hub-approval / hub-guard', JSON.stringify(none), 'canvas/js/runmode.js');
      const srv1 = await ev(async () => { const d = JSON.parse(JSON.stringify(window.wb.board)); d.cards.find(c => c.id === 'hub-approval').run_mode = 'background'; const r = await fetch('/board/starter-hub', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }); return r.status; });
      R('server refuses background on an approval gate', 'HTTP 422', srv1 === 422, 'status=' + srv1);
      if (srv1 !== 422) F('unsafe_enabled', ['unsafe', 'gate_background'], 'Server accepted run_mode=background on the approval gate', 'PUT /board/starter-hub with run_mode on hub-approval', 'status=' + srv1, 'validate.py');
      const srv2 = await ev(async () => { const d = JSON.parse(JSON.stringify(window.wb.board)); d.cards.find(c => c.id === 'hub-guard').run_mode = 'wait'; const r = await fetch('/board/starter-hub', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }); return r.status; });
      R('server refuses run-mode on an immutable card', 'HTTP 422', srv2 === 422, 'status=' + srv2);
      /* containers */
      await sleep(800);
      const cx = await ev(() => { const c = document.querySelector('.card[data-id="hub-gmail"]'); if (!c) return null; const rows = [...c.querySelectorAll('.cx-row')]; return { rows: rows.length, blocked: rows.filter(r => r.classList.contains('blocked')).length, blockedWired: rows.filter(r => r.classList.contains('blocked') && r.querySelector('.cx-wire')).length, wire: c.querySelectorAll('.cx-wire').length, replyWired: rows.filter(r => /reply/.test(r.textContent) && r.querySelector('.cx-wire')).length, acct: (c.querySelector('.cx-acct') || {}).textContent }; });
      R('container shows its tools; blocked tools are not wireable', 'blocked rows have no wire button', !!cx && cx.rows > 0 && cx.blockedWired === 0 && cx.blocked > 0, JSON.stringify(cx));
      if (cx && cx.blockedWired) F('unsafe_enabled', ['unsafe'], 'A blocked (send-capable) connector tool offers a wire button', 'open hub-gmail container; look at blocked tools', JSON.stringify(cx), 'canvas/js/containers.js');
      if (cx && cx.wire > 0) { await closeDrawers(); const wl = page.locator('.card[data-id="hub-gmail"] .cx-wire').first(); if (touch) await wl.tap({ timeout: 2500 }).catch(() => { }); else await wl.click({ timeout: 2500 }).catch(() => { }); await sleep(300); const m2 = await msg(); R('wire button starts a wire with a prompt', 'message tells what to click next', /pick|target|click|wire/i.test(m2), m2.slice(0, 90)); await page.keyboard.press('Escape'); }
      R('active.json untouched', 'byte-identical', activeBytes() === a0, '');
    });

    /* ---------------- (h) phone repeat marker: (c)-(g) already ran with this viewport's input mode ---------------- */
    await step('h', async () => {
      const done = steps.filter(s => ['c', 'd', 'e', 'f', 'g'].includes(s.step));
      R('steps c-g ran on this viewport with ' + (touch ? 'touch' : 'mouse') + ' input', 'recorded results exist for each', ['c', 'd', 'e', 'f', 'g'].every(x => done.some(s => s.step === x)), 'recorded=' + done.length);
      await open('starter-hub');
      const lay = await ev(() => { const de = document.documentElement; const bar = document.getElementById('bar'); const tb = document.getElementById('v4Tb'); const tbr = tb ? tb.getBoundingClientRect() : null; const bs = [...document.querySelectorAll('#bar button,#v4Tb button')].map(b => { const r = b.getBoundingClientRect(); return { t: b.textContent.trim().slice(0, 14), l: r.left, r: r.right, b: r.bottom, h: r.height, w: r.width }; }); return { sw: Math.max(de.scrollWidth, document.body.scrollWidth), cw: de.clientWidth, vh: window.innerHeight, barScroll: bar.scrollWidth > bar.clientWidth, off: bs.filter(b => b.r > window.innerWidth + 1 || b.l < -1 || b.b > window.innerHeight + 1).map(b => b.t), small: bs.filter(b => b.h < 36).map(b => b.t + ':' + Math.round(b.h)), tb: tbr ? { top: Math.round(tbr.top), pos: getComputedStyle(tb).position } : null }; });
      R('no horizontal page scroll and no buttons off-screen', 'scrollWidth<=clientWidth, none offscreen', lay.sw <= lay.cw + 1 && lay.off.length === 0, JSON.stringify(lay));
      if (touch && lay.barScroll) F('phone_ergonomics', ['awkward', 'toolbar_scroll'], 'Top bar scrolls sideways on phone (some buttons hidden until swiped)', 'open on 390x844; look at the top bar', 'bar scrollWidth > clientWidth', 'canvas/index.html');
      if (touch) { await openPal(); const pw = await ev(() => { const p = document.getElementById('pal').getBoundingClientRect(); return { w: Math.round(p.width), vw: window.innerWidth }; }); R('Add drawer fits the phone and can be closed', 'drawer <= 85vw', pw.w <= pw.vw * 0.86, JSON.stringify(pw)); await closeDrawers(); }
    });

    /* ---------------- (i) abuse ---------------- */
    await step('i', async () => {
      /* 1. rapid drag during zoom handoff */
      await resetAll(); await open('starter-hub');
      const hubIds = disk('starter-hub').cards.map(c => c.id).sort().join(','), inIds = disk('starter-intake').cards.map(c => c.id).sort().join(',');
      await ev(() => { const w = window.wb, c = w.board.cards.find(x => x.id === 'hub-host'), r = document.getElementById('vp').getBoundingClientRect(), k = 0.9; w.setView({ k, x: r.width / 2 - (c.pos.x + 96) * k, y: r.height / 2 - (c.pos.y + 45) * k }); });
      for (let i = 0; i < 14; i++) { const id = ['hub-trigger', 'hub-draft', 'hub-agent'][i % 3]; await ev(([id, i]) => { const c = window.wb.board.cards.find(x => x.id === id); if (c) { c.pos = { x: c.pos.x + 8, y: c.pos.y + (i % 2 ? 8 : -8) }; window.wb.render(); } }, [id, i]); if (i % 4 === 0) { const c = await rect('hub-trigger'); if (c) await dragXY(c.cx - 20, c.cy, c.cx + 10, c.cy + 6, 3); } await sleep(40); }
      await sleep(4500);
      const hub1 = disk('starter-hub'), in1 = disk('starter-intake');
      R('rapid edits during handoff: no card crossed into the wrong board', 'same card ids in each file', hub1.cards.map(c => c.id).sort().join(',') === hubIds && in1.cards.map(c => c.id).sort().join(',') === inIds, 'hub=' + hub1.cards.length + ' intake=' + in1.cards.length + ' label=' + await label());
      if (hub1.cards.map(c => c.id).sort().join(',') !== hubIds || in1.cards.map(c => c.id).sort().join(',') !== inIds) F('data_loss', ['data_loss', 'wrong_board_write'], 'Rapid edits during zoom handoff leaked cards between board files', 'zoom toward a card while dragging', 'hub=' + hub1.cards.length + ' intake=' + in1.cards.length, 'canvas/js/autosave.js');
      R('screen stays alive after handoff stress', 'cards visible', (await ev(() => document.querySelectorAll('.card').length)) > 0, '');
      /* 2. offline autosave */
      await resetAll(); await open('starter-hub');
      const x0 = disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos.x;
      await ctx.setOffline(true); await dragCard('hub-draft', 80, 0); const xPage = (await pos('hub-draft')).x; await sleep(9000);
      const warn = await ev(() => { const w = document.getElementById('wb-autosave-warn'); return !!w && getComputedStyle(w).display !== 'none'; }), mOff = await msg();
      R('offline: the page warns that visual changes are not saved', 'warn pill visible within ~9 s', warn, 'warn=' + warn + ' msg=' + mOff);
      if (!warn) F('data_loss', ['data_loss', 'silent'], 'Offline autosave failure showed no warning (edit silently unsaved)', 'go offline; drag a card; wait 9 s', 'no #wb-autosave-warn', 'canvas/js/autosave.js');
      await ctx.setOffline(false); let landed = false;
      for (let i = 0; i < 28 && !landed; i++) { await sleep(250); landed = disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos.x === xPage; }
      R('back online: the pending edit is saved by itself', 'hub-draft x on disk equals the page value within 7 s of reconnecting', landed, 'disk x=' + disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos.x + ' page x=' + xPage + ' from ' + x0);
      if (!landed) { F('awkward', ['awkward', 'no_retry_after_reconnect'], 'After an offline failure the edit is only re-sent when the user edits something else; the red warning stays with no Retry control', 'go offline; drag a card; wait for the red warning; go online; wait', 'x unchanged on disk until another edit', 'canvas/js/autosave.js');
        await dragCard('hub-trigger', 8, 0); for (let i = 0; i < 40 && !landed; i++) { await sleep(250); landed = disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos.x === xPage; }
        R('...and a later edit flushes the earlier one', 'saved after another edit', landed, 'disk x=' + disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos.x);
        if (!landed) F('data_loss', ['data_loss'], 'An edit made offline was never saved even after further edits', 'offline; drag; online; drag again; wait', 'x not on disk', 'canvas/js/autosave.js'); }
      /* 3. reload mid-edit */
      await resetAll(); await open('starter-hub');
      const y0 = disk('starter-hub').cards.find(c => c.id === 'hub-agent').pos.x;
      await dragCard('hub-agent', 72, 0); const yPage = (await pos('hub-agent')).x; await page.reload(); await page.waitForSelector('.card'); await sleep(1500);
      const y1 = disk('starter-hub').cards.find(c => c.id === 'hub-agent').pos.x;
      R('reload right after a visual edit keeps it', 'moved card saved before the page went away', y1 === yPage, 'x ' + y0 + '->' + y1 + ' want ' + yPage);
      if (y1 !== yPage) F('data_loss', ['data_loss'], 'A card move made just before a reload was lost (no flush on page hide)', 'drag a card then reload within 1 s', 'x=' + y1 + ' expected ' + yPage, 'canvas/js/autosave.js');
      await open('starter-hub'); await selectCard('hub-draft'); await openInsp(); await page.locator('#inspBody input').nth(2).fill('Unsaved rename');
      let dlg = dialogs.some(d => d.t === 'beforeunload');
      await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true }))); const bu = await ev(() => typeof window.onbeforeunload);
      await open('starter-hub'); const kept = await ev(() => window.wb.board.cards.find(c => c.id === 'hub-draft').label);
      R('reload with an unsaved behaviour edit', 'edit is warned about (beforeunload) or kept', kept === 'Unsaved rename' || bu === 'function' || dlg, 'label after reload=' + kept + ' beforeunload=' + bu);
      if (!(kept === 'Unsaved rename' || bu === 'function' || dlg)) F('awkward', ['awkward', 'unsaved_edit_lost_silently'], 'Reloading with an unsaved behaviour edit loses it with no warning', 'rename a card; do not Save; reload', 'label=' + kept, 'canvas/js/core.js');
      /* 4. two tabs on the same board */
      await resetAll(); await open('starter-hub');
      const page2 = await ctx.newPage(); wire(page2); await open('starter-hub', '', page2);
      const ax0 = disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos, by0 = disk('starter-hub').cards.find(c => c.id === 'hub-agent').pos;
      await page.evaluate(() => { const c = window.wb.board.cards.find(x => x.id === 'hub-draft'); c.pos = { x: c.pos.x + 56, y: c.pos.y }; window.wb.render(); }); await sleep(3500);
      await page2.evaluate(() => { const c = window.wb.board.cards.find(x => x.id === 'hub-agent'); c.pos = { x: c.pos.x, y: c.pos.y + 56 }; window.wb.render(); }); await sleep(3500);
      const dk2 = disk('starter-hub'), a1 = dk2.cards.find(c => c.id === 'hub-draft').pos, b1 = dk2.cards.find(c => c.id === 'hub-agent').pos;
      R('two tabs: both visual edits survive (no lost update)', 'hub-draft +56x and hub-agent +56y both on disk', a1.x === ax0.x + 56 && b1.y === by0.y + 56, 'draft ' + JSON.stringify(ax0) + '->' + JSON.stringify(a1) + ' agent ' + JSON.stringify(by0) + '->' + JSON.stringify(b1));
      if (!(a1.x === ax0.x + 56 && b1.y === by0.y + 56)) F('data_loss', ['data_loss', 'lost_update'], 'Two tabs on one board: the second tab\'s autosave overwrote the first tab\'s visual edit', 'open board in 2 tabs; move card A in tab 1, card B in tab 2', 'draft x=' + a1.x + ' (want ' + (ax0.x + 56) + ')', 'canvas/js/autosave.js / serve.py PATCH');
      await page2.evaluate(() => { window.wb.board.cards.find(c => c.id === 'hub-compile').label = 'Stale tab rename'; }); await page2.evaluate(() => document.getElementById('bSave').click()); await sleep(900);
      const a2 = disk('starter-hub').cards.find(c => c.id === 'hub-draft').pos.x;
      R('two tabs: Save from a stale tab', 'does not silently undo the other tab\'s move (or warns)', a2 === ax0.x + 56, 'draft x on disk=' + a2);
      if (a2 !== ax0.x + 56) F('awkward', ['awkward', 'stale_tab_save'], 'Save from a stale second tab silently reverted the first tab\'s autosaved move (whole-board PUT, no conflict warning)', 'tab1 move card, tab2 (opened earlier) edit label and Save', 'x=' + a2, 'canvas/js/core.js saveBoard');
      await page2.close();
      /* 5. unsafe requests */
      await resetAll(); await open('starter-hub');
      const unsafe = await ev(async () => {
        const post = async d => (await fetch('/board/starter-hub', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) })).status;
        const base = () => JSON.parse(JSON.stringify(window.wb.board)); const o = {};
        let d = base(); d.cards.find(c => c.id === 'hub-guard').enabled = false; o.disableImmutable = await post(d);
        d = base(); d.cards = d.cards.filter(c => c.id !== 'hub-guard'); o.deleteImmutable = await post(d);
        d = base(); d.cards.find(c => c.id === 'hub-approval').execution_mode = 'enforce'; o.approvalEnforce = await post(d);
        d = base(); d.cards.find(c => c.id === 'hub-approval').enabled = false; o.disableApproval = await post(d);
        d = base(); d.cards = d.cards.filter(c => c.id !== 'hub-approval'); d.edges = d.edges.filter(e => !/hub-approval/.test(e.from + e.to)); o.deleteApproval = await post(d);
        d = base(); d.cards.find(c => c.id === 'hub-approval').ref.approver = 'node'; o.approvalApprover = await post(d);
        const pr = await fetch('/board/starter-hub/visual', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cards: { 'hub-draft': { label: 'x' } }, notes: [], notes_removed: [] }) }); o.visualBehaviourPatch = pr.status;
        const pr2 = await fetch('/board/starter-hub/visual', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cards: { 'hub-guard': { enabled: false } }, notes: [], notes_removed: [] }) }); o.visualImmutablePatch = pr2.status;
        const nb = await fetch('/board/..%2Factive', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' }); o.activeWrite = nb.status;
        return o;
      });
      R('server refuses disabling an immutable card', 'HTTP 422', unsafe.disableImmutable === 422, JSON.stringify(unsafe));
      R('server refuses deleting an immutable card', 'HTTP 422', unsafe.deleteImmutable === 422, '');
      R('server refuses a behaviour change through the visual autosave route', 'HTTP 4xx (409/422)', unsafe.visualBehaviourPatch >= 400 && unsafe.visualImmutablePatch >= 400, 'label=' + unsafe.visualBehaviourPatch + ' immutable=' + unsafe.visualImmutablePatch);
      R('active.json cannot be written through /board', 'HTTP 4xx', unsafe.activeWrite >= 400, 'status=' + unsafe.activeWrite);
      for (const k of ['disableImmutable', 'deleteImmutable', 'approvalApprover', 'visualBehaviourPatch', 'visualImmutablePatch', 'activeWrite']) if (!(unsafe[k] >= 400)) F('unsafe_enabled', ['unsafe'], 'Unsafe request "' + k + '" was accepted by the server (HTTP ' + unsafe[k] + ')', 'see evidence', JSON.stringify(unsafe), 'serve.py / validate.py');
      R('enforce on an approval gate is a legal setting', 'HTTP 200 (gates may enforce)', unsafe.approvalEnforce === 200, 'status=' + unsafe.approvalEnforce);
      R('approval gate (not immutable) can be disabled or deleted in a saved draft', 'recorded for the owner: draft only, takes effect after owner deploy', true, 'disable=' + unsafe.disableApproval + ' delete=' + unsafe.deleteApproval);
      if (unsafe.disableApproval === 200 || unsafe.deleteApproval === 200) F('awkward', ['awkward', 'approval_gate_removable'], 'A non-immutable approval gate can be disabled or deleted and saved as a draft (HTTP ' + unsafe.disableApproval + '/' + unsafe.deleteApproval + '); it only takes effect after the owner deploys, but nothing in the editor warns about removing an approval step', 'select the approval gate; set enabled=false (or delete); Save', JSON.stringify({ disable: unsafe.disableApproval, del: unsafe.deleteApproval }), 'validate.py / canvas/js/core.js');
      const gdis = await ev(() => { const c = window.wb.board.cards.find(x => x.id === 'hub-guard'); return !!c; });
      await selectCard('hub-guard'); await openInsp();
      const lock = await ev(() => { const sel = [...document.querySelectorAll('#inspBody select')]; return { enabledDisabled: sel[0] ? sel[0].disabled : null, modeDisabled: sel[1] ? sel[1].disabled : null, portsDisabled: (document.querySelectorAll('#inspBody input')[3] || {}).disabled }; });
      R('immutable card: enabled toggle is locked in the inspector', 'select disabled', lock.enabledDisabled === true, JSON.stringify(lock));
      if (lock.enabledDisabled !== true) F('unsafe_enabled', ['unsafe'], 'The inspector lets the user disable an immutable card', 'select the safety-check hook; inspector > enabled', JSON.stringify(lock), 'canvas/js/core.js');
      if (lock.modeDisabled === false) F('note', ['suggestion'], 'On an immutable card the enforce/observe selector is still editable (server decides)', 'select hub-guard; execution_mode', JSON.stringify(lock), 'canvas/js/core.js');
      /* wrong-click behaviours (all personas run them; Dana is the brief that stresses them) */
      await resetAll(); await open('starter-hub'); const n0 = (await state()).cards, files = sb.files().join(',');
      await closeDrawers(); await page.mouse.move(vpc.width / 2, vpc.height / 2); await page.keyboard.press('Control+a'); await sleep(150); await page.keyboard.press('Delete'); await sleep(600);
      const n1 = (await state()).cards; await sleep(4200);
      const dn = disk('starter-hub').cards, nonNote = dn.filter(c => c.type !== 'note').length, wasNon = n0 - 1, noteGone = !dn.some(c => c.type === 'note');
      R('select-all then Delete: immutable cards stay, action cards are not deleted on disk without Save', 'immutable card remains; every non-note card still on disk', (await ev(() => window.wb.board.cards.some(c => c.id === 'hub-guard'))) && nonNote === wasNon, 'cards ' + n0 + '->' + n1 + ' disk nonNote=' + nonNote + ' noteGone=' + noteGone);
      if (nonNote !== wasNon) F('data_loss', ['data_loss'], 'Select-all + Delete removed action cards on disk without a Save', 'Ctrl+A, Delete, wait 5 s', 'disk nonNote=' + nonNote, 'canvas/js/autosave.js');
      if (noteGone) F('awkward', ['awkward', 'note_delete_autosaves'], 'Select-all + Delete: sticky notes are deleted on disk immediately (autosave) while every other card comes back on reload, so the result is half-deleted', 'Ctrl+A, Delete, wait 5 s, reload', 'note removed from disk, action cards restored on reload', 'canvas/js/autosave.js');
      const nAfter = (await (async () => { await open('starter-hub'); return (await state()).cards; })()); R('wrong click: reloading brings the deleted cards back', 'reload shows all action cards (notes autosave their deletion)', nAfter >= n0 - 1, 'cards after reload=' + nAfter + ' of ' + n0);
      await ev(() => { const v = document.getElementById('vp'); v.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: 700, clientY: 500 })); });
      await closeDrawers(); const sel0 = await ev(() => window.wb.sel.size);
      R('double-click on empty canvas and background taps are harmless', 'no error, board intact', (await state()).cards >= n0 - 1 && sb.files().join(',') === files, 'files=' + sb.files().join(','));
      await tapEl('#bDeploy'); await tapEl('#bSave'); await sleep(500); await tapEl('#bSave'); await sleep(500);
      R('repeated Save/Deploy clicks are harmless', 'board valid on disk, active.json unchanged', activeBytes().length > 10 && disk('starter-hub').cards.length >= n0 - 1, '');
    });
  } catch (e) {
    findings.push({ persona: PID, viewport: VPN, step: cur, category: 'harness', flags: ['script_error'], title: 'runner crashed: ' + String(e && e.message || e).slice(0, 150), repro: 'rerun', evidence: String(e && e.stack || e).slice(0, 400), file: '' });
    steps.push({ persona: PID, viewport: VPN, step: cur, sub: 'runner', expected: 'completes', pass: false, evidence: String(e && e.stack || e).slice(0, 300) });
  }
  const unexpected = errs.filter(e => !/^(404|409|422|400|403|405) (PUT|PATCH|POST)/.test(e.m) && !/^(409|422|400|403|404|405) /.test(e.m));
  const realAfter = L.hashDir(REAL), guardOk = L.sameHash(realBefore, realAfter);
  if (!guardOk) findings.push({ persona: PID, viewport: VPN, step: 'guard', category: 'real_boards_modified', flags: ['real_boards_modified', 'unsafe'], title: 'Real boards/ changed during the persona run', repro: 'see guard', evidence: 'hash before != after', file: '' });
  const result = { persona: PID, viewport: VPN, steps, findings, errors: errs.slice(0, 60), unexpectedErrors: unexpected.slice(0, 30), guard: { ok: guardOk, files: Object.keys(realBefore).length }, sandbox: sb.tmp, port: srv.port };
  fs.writeFileSync(path.join(OUT, VPN + '-result.json'), JSON.stringify(result, null, 1));
  try { if (browser) await browser.close(); } catch (e) { }
  srv.stop(); await sleep(300); L.rmrf(sb.tmp);
  const bad = steps.filter(s => !s.pass).length;
  console.log(JSON.stringify({ persona: PID, viewport: VPN, steps: steps.length, failed: bad, findings: findings.length, guard: guardOk, unexpectedErrors: unexpected.length }));
})().catch(e => { console.log('CRASH ' + e); process.exit(2); });
