// v2 runner (real-query fact + live set). Reuses the Phase 1 runner library, setups, hook and shared prompt.
//
//   node scripts/run.mjs --run-id pilot-1 --create --arms web,firecrawl_noalex,firecrawl_alex,flaim --ids F001,L002,...
//   node scripts/run.mjs --run-id pilot-1 --preflight      one probe per setup (MCP connected, tools present, hook refuses Alexandria)
//   node scripts/run.mjs --run-id pilot-1 [--conc 8]       answer every expected cell -> runs/<id>/answers/<q>/<setup>/
// The manifest pins model, effort, setups, the question file and code by hash; a run refuses to continue on drift. Resumable.
import { readFileSync, writeFileSync, existsSync, mkdirSync, chmodSync, readdirSync, renameSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { runClaude } from './runner_lib.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const R = (...s) => join(ROOT, ...s);
const argv = process.argv;
const arg = (n, d = null) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : d; };
const has = (n) => argv.includes(n);
const readJSON = (p) => JSON.parse(readFileSync(p, 'utf8'));
const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex');
const RUN = arg('--run-id'); if (!RUN) throw new Error('--run-id is required');
const RUNDIR = R('runs', RUN); mkdirSync(RUNDIR, { recursive: true });
const MPATH = join(RUNDIR, 'manifest.json');
const QFILE = arg('--questions', null) || (existsSync(join(RUNDIR, 'manifest.json')) ? readJSON(join(RUNDIR, 'manifest.json')).questions.path : 'data/questions/questions_300.jsonl');
const CODE = ['scripts/run.mjs', 'scripts/runner_lib.mjs', 'hooks/block_alexandria.py', 'config/arms.json', 'config/system-note.txt'];
const log = (...a) => { const l = `${new Date().toISOString()} ${a.join(' ')}`; console.log(l); writeFileSync(join(RUNDIR, 'run.log'), l + '\n', { flag: 'a' }); };
const QALL = readFileSync(R(QFILE), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));

if (has('--create')) {
  if (existsSync(MPATH)) throw new Error(`${MPATH} exists; a run id is used once`);
  const arms = arg('--arms').split(',');
  const ids = arg('--ids') ? arg('--ids').split(',') : QALL.map((q) => q.id);
  const unknown = ids.filter((i) => !QALL.some((q) => q.id === i)); if (unknown.length) throw new Error(`unknown ids ${unknown}`);
  const m = { run_id: RUN, created_at: new Date().toISOString(), model: arg('--model', 'claude-opus-5-5'), effort: arg('--effort', 'high'), arms,
    questions: { path: QFILE, sha256: sha(R(QFILE)), selected: ids.length }, code_sha256: Object.fromEntries(CODE.map((c) => [c, sha(R(c))])),
    expected: ids.flatMap((id) => arms.map((arm) => ({ id, arm }))) };
  writeFileSync(MPATH, JSON.stringify(m, null, 1));
  console.log(`${MPATH}: ${m.expected.length} cells (${ids.length} questions x ${arms.length} setups), model ${m.model}, effort ${m.effort}`);
  process.exit(0);
}

const M = readJSON(MPATH);
const drift = [];
if (sha(R(M.questions.path)) !== M.questions.sha256) drift.push('question file changed');
for (const [c, h] of Object.entries(M.code_sha256)) if (sha(R(c)) !== h) drift.push(`code changed: ${c}`);
if (drift.length) { console.error('manifest drift:', drift.join('; ')); process.exit(1); }
const cfg = readJSON(R('config', 'arms.json'));

function envKey(name) {
  if (process.env[name]) return process.env[name];
  for (const f of [R('..', 'real-query-eval', '.env.local'), R('..', 'harness', '.env.eval')]) {
    if (!existsSync(f)) continue;
    const m = readFileSync(f, 'utf8').match(new RegExp(`^${name}=(.*)$`, 'm')); if (m) return m[1].trim().replace(/^['"]|['"]$/g, '');
  }
  throw new Error(`${name} not found`);
}
// MCP configs live in the run folder (mode 600: the Firecrawl URL embeds the key). Flaim reuses the claude CLI sign-in.
const SERVERS = {
  firecrawl: () => ({ type: 'http', url: `https://mcp.firecrawl.dev/${envKey('FIRECRAWL_API_KEY')}/v2/mcp` }),
  flaim: () => ({ type: 'http', url: 'https://api.flaim.app/mcp' }),
};
function mcpFile(name) {
  const p = join(RUNDIR, 'mcp', `${name}.json`); mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, JSON.stringify(name === 'none' ? { mcpServers: {} } : { mcpServers: { [name]: SERVERS[name]() } }, null, 1)); chmodSync(p, 0o600); return p;
}
function hookSettings(script) {
  const p = join(RUNDIR, 'mcp', `hook-${script.replace(/\W/g, '_')}.json`);
  writeFileSync(p, JSON.stringify({ hooks: { PreToolUse: [{ matcher: 'mcp__firecrawl__.*', hooks: [{ type: 'command', command: `python3 ${R(script)}` }] }] } }, null, 1));
  return p;
}
const baseNote = readFileSync(R('config', 'system-note.txt'), 'utf8').trim();
const ARMS = Object.fromEntries(M.arms.map((n) => {
  const a = cfg.arms[n];
  const note = `${baseNote}\n\nTools available in this session (nothing else; shell, file and code tools are not available):\n${a.inventory.map((l) => `- ${l}`).join('\n')}`;
  return [n, { ...a, mcpFiles: a.mcp.length ? a.mcp.map(mcpFile) : [mcpFile('none')], settingsFile: a.hook ? hookSettings(a.hook) : null,
    disallowed: cfg.disallowBuiltins.concat(a.disallow || []).join(','), note }];
}));
const servedOk = (meta) => meta?.model === M.model;
const call = (A, prompt, cwd, maxTurns) => runClaude({ prompt, model: M.model, effort: M.effort, mcpConfigs: A.mcpFiles, allowedTools: A.tools,
  disallowedTools: A.disallowed, systemNote: A.note, settingsFile: A.settingsFile, cwd, maxTurns, timeoutMs: cfg.timeoutMs });

if (has('--preflight')) {
  const PROBE = {
    web: 'Use WebSearch once to search "NFL Week 5 2026 scores" and reply with the number of results.',
    firecrawl_alex: 'Call firecrawl_find_tools once to look for NFL fantasy weekly projections tools and reply with how many tools it returned.',
    firecrawl_noalex: 'Do two things and report exactly what happened for each. 1) Call firecrawl_scrape with an alexandria body {"provider":"cbssports-com","capability":"fantasy-sports-rankings/stats","options":{"position":"WR","scoring":"PPR","limit":3}}. 2) Call firecrawl_search with the query "NFL Week 5 2026 final scores".',
    flaim: 'Call get_user_session once and reply with the names of the leagues it lists.',
  };
  const HOOK = /Alexandria (execution|tool discovery|search) is disabled in this session/i;
  const BAD = /\b401\b|unauthori[sz]ed|forbidden|invalid[_ ]?(request|params|argument)|"error"\s*:|provider_error|not_found/i;
  const bad = (t) => t.is_error || BAD.test(String(t.output).slice(0, 2000));
  let ok = true;
  for (const [n, A] of Object.entries(ARMS)) {
    const { result, meta, trace } = await call(A, PROBE[n], join(RUNDIR, 'preflight', n), 4);
    const mcpTools = (meta?.tools || []).filter((t) => t.startsWith('mcp__'));
    const missing = A.tools.filter((t) => t.startsWith('mcp__') && !mcpTools.includes(t));
    let good;
    if (n === 'firecrawl_noalex') {
      const hookRefusal = trace.some((t) => t.name.endsWith('firecrawl_scrape') && t.input?.alexandria && HOOK.test(String(t.output)));
      const ordinaryOk = trace.some((t) => /firecrawl_(search|scrape)$/.test(t.name) && !t.input?.alexandria && !bad(t) && !HOOK.test(String(t.output)));
      const advertised = trace.some((t) => t.name.endsWith('firecrawl_search') && /"tools"\s*:\s*\[\s*\{\s*"provider"/.test(String(t.output)));
      good = servedOk(meta) && !missing.length && hookRefusal && ordinaryOk && !advertised;
    } else good = servedOk(meta) && !missing.length && trace.length > 0 && !trace.some(bad);
    console.log(`${n.padEnd(18)} ${good ? 'OK  ' : 'FAIL'} served=${meta?.model} mcp=${JSON.stringify((meta?.mcp_servers || []).map((s) => `${s.name}:${s.status}`))} missing=${missing.join(',') || 'none'} calls=${trace.map((t) => t.name.replace(/^mcp__[^_]+__/, '')).join(' ')} answer=${JSON.stringify((result.answer || '').slice(0, 120))}`);
    if (!good) ok = false;
  }
  console.log(ok ? 'PREFLIGHT OK' : 'PREFLIGHT FAILED');
  process.exit(ok ? 0 : 1);
}

const Q = Object.fromEntries(QALL.map((q) => [q.id, q]));
const cells = M.expected.map((c) => ({ ...c, dir: join(RUNDIR, 'answers', c.id, c.arm) }));
const done = (c) => existsSync(join(c.dir, 'result.json')) && !readJSON(join(c.dir, 'result.json')).is_error;
const only = arg('--only-arm');
const todo = cells.filter((c) => (has('--force') || !done(c)) && (!only || c.arm === only));
const conc = Number(arg('--conc', '8'));
log(`run ${RUN}: ${cells.length} cells, ${todo.length} to do; model ${M.model}, effort ${M.effort}; conc ${conc}`);
let i = 0, n = 0, cost = 0;
async function worker() {
  while (i < todo.length) {
    const c = todo[i++];
    if (existsSync(join(c.dir, 'result.json'))) {  // keep earlier attempts (and their cost)
      const k = existsSync(join(c.dir, 'attempts')) ? readdirSync(join(c.dir, 'attempts')).length + 1 : 1;
      const dest = join(c.dir, 'attempts', String(k)); mkdirSync(dest, { recursive: true });
      for (const f of readdirSync(c.dir)) if (f !== 'attempts') renameSync(join(c.dir, f), join(dest, f));
    }
    const { result, meta } = await call(ARMS[c.arm], Q[c.id].prompt, c.dir, cfg.maxTurns);
    if (!servedOk(meta)) { result.is_error = true; result.model_mismatch = meta?.model || null; writeFileSync(join(c.dir, 'result.json'), JSON.stringify(result, null, 1)); }
    cost += result.total_cost_usd || 0; n++;
    log(`${String(n).padStart(4)}/${todo.length} ${c.id} ${c.arm} ${result.is_error ? 'ERROR' : 'ok'} ${(result.wall || 0).toFixed(0)}s tools=${result.tool_calls} $${(result.total_cost_usd || 0).toFixed(3)} total=$${cost.toFixed(2)}`);
  }
}
await Promise.all(Array.from({ length: conc }, worker));
log(`run ${RUN} done: ${n} sessions, $${cost.toFixed(2)}`);
