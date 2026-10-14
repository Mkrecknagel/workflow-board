/* Persona harness library (N5). Sandbox = TEMP COPY of the workspace + seeded starter-library boards + a server on a free loopback port
   (never 8765). The real boards/ directory is hashed before and after by guard.py / guard.js; any difference fails the run.
   Driver: playwright-core (the engine behind playwright-cli) launched headless from node, because a persona script is hundreds of
   stateful steps with CDP touch and offline control that the one-shot playwright-cli command line cannot express; smoke.sh/ test_harness
   also drive one step through the playwright-cli binary itself. No images are read: screenshots are only SAVED as evidence paths. */
const fs = require('fs'), os = require('os'), path = require('path'), net = require('net'), crypto = require('crypto');
const { spawn, spawnSync } = require('child_process');
const SRC = path.resolve(__dirname, '..', '..');
const PW = process.env.PW || 'playwright-core';
const seed = require('./seed.js');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const sha = f => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
const rmrf = d => { try { fs.rmSync(d, { recursive: true, force: true }); } catch (e) { } };
const freePort = () => new Promise(r => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });

/* hash of every file under real boards/ (recursive, includes .history and active.json) */
function hashDir(dir) {
  const out = {};
  const walk = d => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else out[path.relative(dir, p).replace(/\\/g, '/')] = sha(p); } };
  if (fs.existsSync(dir)) walk(dir);
  return out;
}
const sameHash = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function makeSandbox(label) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-persona-' + label + '-'));
  const wb = path.join(tmp, 'wb');
  const skip = new Set(['__pycache__', 'runs', 'tickets', 'proposals', 'node_modules']);
  fs.cpSync(SRC, wb, { recursive: true, filter: s => !skip.has(path.basename(s)) && !s.endsWith('.png') && !s.split(path.sep).join('/').endsWith('docs/personas') });
  /* a clean board set: remove every real board and history, then seed the starter library */
  const bd = path.join(wb, 'boards');
  for (const f of fs.readdirSync(bd)) rmrf(path.join(bd, f));
  fs.mkdirSync(bd, { recursive: true });
  seed.write(bd);
  const sb = { tmp, wb, bd, boardFile: n => path.join(bd, n + '.json'), disk: n => JSON.parse(fs.readFileSync(path.join(bd, n + '.json'), 'utf8')),
    exists: n => fs.existsSync(path.join(bd, n + '.json')), files: () => fs.readdirSync(bd).filter(f => f.endsWith('.json')).sort() };
  sb.reset = () => { for (const f of fs.readdirSync(bd)) rmrf(path.join(bd, f)); seed.write(bd); sb.writeActive('starter-hub'); };
  sb.writeActive = name => {
    const py = spawnSync('python', ['-c', "import json,sys;sys.path.insert(0,'.');import serve_v4_runs as R;d=json.load(open('boards/%s.json'));json.dump({'board':'%s','hash':R.semantic_hash(d),'deployed_at':'2026-10-05T00:00:00.000Z','by':'owner','hash_basis':'semantic v5'},open('boards/active.json','w'),indent=2)".replace(/%s/g, name)], { cwd: wb });
    if (py.status !== 0) throw new Error('active.json seed failed: ' + String(py.stderr).slice(0, 200));
  };
  sb.writeActive('starter-hub');
  return sb;
}

async function startServer(sb) {
  const port = await freePort();
  if (port === 8765) throw new Error('refusing port 8765');
  const BASE = 'http://127.0.0.1:' + port;
  const proc = spawn('python', [path.join(sb.wb, 'serve.py')], { cwd: sb.wb, stdio: 'ignore', env: { ...process.env, BOARD_PORT: String(port), WB_TICKETS_DIR: path.join(sb.tmp, 'tickets') } });
  let up = false;
  for (let i = 0; i < 75 && !up; i++) { try { const r = await fetch(BASE + '/boards'); if (r.ok) up = true; } catch (e) { } if (!up) await sleep(200); }
  if (!up) { proc.kill(); throw new Error('server did not start'); }
  return { port, BASE, proc, stop: () => { try { proc.kill(); } catch (e) { } } };
}

module.exports = { SRC, PW, sleep, sha, rmrf, freePort, hashDir, sameHash, makeSandbox, startServer };
