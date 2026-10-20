// Headless Claude Code runner for Phase 1 (self-contained; no shared harness). One call = one isolated session:
//   --model and --effort pinned on every call (no defaults), --strict-mcp-config always (an empty config for the web arm),
//   reads limited to this session's own persisted tool outputs, optional --settings file (hooks) per arm.
// Writes stream.jsonl, stderr.txt, result.json, trace.json, meta.json (what actually served the session) into cwd.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';

const writeJSON = (p, o) => writeFileSync(p, JSON.stringify(o, null, 1));

// Claude Code keeps a session's persisted tool outputs under ~/.claude/projects/<cwd slug>/; each run cell has its own cwd.
export const sessionReadRule = (cwd) => `Read(/${join(homedir(), '.claude', 'projects', cwd.replace(/[^A-Za-z0-9]/g, '-'))}/**)`;

export function runClaude({ prompt, model, effort, mcpConfigs, allowedTools, disallowedTools = '', systemNote, settingsFile = null, cwd, maxTurns = 30, timeoutMs = 600000 }) {
  if (!model || !effort) throw new Error('model and effort must be set explicitly');
  if (!mcpConfigs?.length) throw new Error('pass at least one MCP config (an empty one for arms without MCP)');
  mkdirSync(cwd, { recursive: true });
  const tools = [...allowedTools, sessionReadRule(cwd)].join(',');
  const args = ['-p', prompt, '--output-format', 'stream-json', '--verbose', '--max-turns', String(maxTurns), '--model', model, '--effort', effort,
    '--allowedTools', tools, '--strict-mcp-config'];
  if (disallowedTools) args.push('--disallowedTools', disallowedTools);
  for (const c of mcpConfigs) args.push('--mcp-config', c);
  if (settingsFile) args.push('--settings', settingsFile);
  if (systemNote) args.push('--append-system-prompt', systemNote);
  const env = { ...process.env, MCP_CONNECTION_NONBLOCKING: 'false' }; // http MCP servers otherwise connect in the background
  delete env.CLAUDECODE; delete env.CLAUDE_CODE_ENTRYPOINT;
  return new Promise((resolve) => {
    const t0 = Date.now(); let out = '', err = '';
    const p = spawn(process.env.CLAUDE_BIN || 'claude', args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
    const timer = setTimeout(() => p.kill('SIGKILL'), timeoutMs);
    p.stdout.on('data', (d) => (out += d)); p.stderr.on('data', (d) => (err += d));
    p.on('close', (code) => {
      clearTimeout(timer);
      const wall = (Date.now() - t0) / 1000;
      writeFileSync(join(cwd, 'stream.jsonl'), out); writeFileSync(join(cwd, 'stderr.txt'), err);
      const parsed = parseStream(out);
      const timed_out = wall >= timeoutMs / 1000 - 1;
      const result = { ...parsed.result, answer: parsed.answer, wall, timed_out, exit_code: code, tool_calls: parsed.trace.length,
        is_error: Boolean(parsed.result.is_error || timed_out || code !== 0 || !parsed.answer) };
      writeJSON(join(cwd, 'result.json'), result); writeJSON(join(cwd, 'trace.json'), parsed.trace);
      writeJSON(join(cwd, 'meta.json'), { requested: { model, effort }, effort_verified: false, served: parsed.init, finished_at: new Date().toISOString() });
      resolve({ result, meta: parsed.init, trace: parsed.trace });
    });
  });
}

// stream-json -> final answer, result record, init record (model, tools, MCP status) and an ordered tool trace.
export function parseStream(text) {
  const trace = []; const pending = new Map(); let answer = ''; let result = {}; let init = null;
  for (const line of text.split('\n')) {
    if (!line.trim().startsWith('{')) continue;
    let m; try { m = JSON.parse(line); } catch { continue; }
    if (m.type === 'system' && m.subtype === 'init') {
      init = { model: m.model, tools: m.tools, mcp_servers: m.mcp_servers, session_id: m.session_id, claude_code_version: m.claude_code_version ?? null,
        per_turn_effort_active: m.per_turn_effort_active ?? null };  // the session reports no effort value: effort is requested, not verifiable
    } else if (m.type === 'assistant') {
      for (const c of m.message?.content || []) {
        if (c.type === 'text') answer = c.text;
        if (c.type === 'tool_use') { const rec = { id: c.id, name: c.name, input: c.input, output: null, is_error: false }; trace.push(rec); pending.set(c.id, rec); }
      }
    } else if (m.type === 'user') {
      const content = m.message?.content; if (!Array.isArray(content)) continue;
      for (const c of content) if (c.type === 'tool_result') { const rec = pending.get(c.tool_use_id); if (!rec) continue; rec.output = typeof c.content === 'string' ? c.content : JSON.stringify(c.content); rec.is_error = Boolean(c.is_error); pending.delete(c.tool_use_id); }
    } else if (m.type === 'result') {
      result = { total_cost_usd: m.total_cost_usd ?? null, num_turns: m.num_turns ?? null, duration_ms: m.duration_ms ?? null, is_error: Boolean(m.is_error),
        stop_reason: m.stop_reason ?? null, session_id: m.session_id ?? null, usage: m.usage ?? null, modelUsage: m.modelUsage ?? null };
      if (typeof m.result === 'string' && m.result.trim()) answer = m.result;
    }
  }
  return { answer, result, trace, init };
}
