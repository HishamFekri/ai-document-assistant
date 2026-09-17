import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { parseConfig, optionsFor, SCENARIOS } from '../config.js';
import { exampleEnvironment, prepare } from '../run.mjs';
import { createTraffic } from '../traffic.js';
import { aggregate } from '../observations.mjs';
import { buildSummary } from '../summary.js';

const SECRET = 'secret-token-never-print-12345';
function env(scenario = 'reads', updates = {}) { return { ...exampleEnvironment(scenario), ...updates }; }
const response = (status, data, headers = {}) => ({ status, body: JSON.stringify(data), headers, timings: { duration: 12 } });
function rig(scenario = 'reads', updates = {}, responder = () => response(200, {})) {
  const config = parseConfig(env(scenario, updates));
  const calls = [], sleeps = [], metrics = [];
  const traffic = createTraffic(config, {
    request(...args) { calls.push(args); return calls.length === 1 && scenario !== 'health'
      ? response(200, { id: 1 }) : responder(...args); },
    file: (content, filename, type) => ({ content, filename, type }),
    sleep: seconds => sleeps.push(seconds),
    metric: (name, value, tags) => metrics.push({ name, value, tags }),
    abort: message => { throw new Error(message); },
  });
  return { config, calls, sleeps, metrics, traffic };
}

test('missing target and acknowledgement fail closed without echoing input', () => {
  for (const key of ['LOAD_BASE_URL', 'LOAD_TARGET_ACK', 'LOAD_CONFIRMED_ORIGIN', 'LOAD_ALLOWED_HOST', 'LOAD_TARGET_IP', 'LOAD_ENVIRONMENT']) {
    assert.throws(() => parseConfig(env('reads', { [key]: SECRET })), error => !error.message.includes(SECRET));
  }
  assert.throws(() => parseConfig({}));
});

test('production labels, URLs with secrets, aliases, redirects and insecure staging URLs are refused', () => {
  for (const origin of ['https://api.example.com', 'https://api.production.example.test', 'https://prod-staging.example.test',
    'https://www.staging.example.test', 'https://prod2.staging.example.test', 'https://api.-staging.example.test',
    'https://api.staging-.example.test', 'https://api..staging.example.test',
    'http://api.staging.example.test', 'https://user:password@staging.example.test',
    'https://api.staging.example.test/path', 'https://api.staging.example.test?token=secret',
    'https://api.staging.example.test#fragment', 'https://localhost', 'http://0.0.0.0:18000',
    'http://127.1:18000', 'http://2130706433:18000', 'http://[::1]:18000', 'http://127.0.0.1:18000/']) {
    assert.throws(() => parseConfig(env('health', { LOAD_BASE_URL: origin, LOAD_CONFIRMED_ORIGIN: origin,
      LOAD_ALLOWED_HOST: origin.split('://')[1].split(':')[0] })));
  }
});

test('local target needs canonical loopback, dedicated port and no-tunnel acknowledgement', () => {
  const local = { LOAD_BASE_URL: 'http://127.0.0.1:18000', LOAD_CONFIRMED_ORIGIN: 'http://127.0.0.1:18000',
    LOAD_ALLOWED_HOST: '127.0.0.1', LOAD_TARGET_IP: '127.0.0.1', LOAD_ENVIRONMENT: 'isolated-local',
    LOAD_LOCAL_ACK: 'dedicated-stack-no-production-tunnel' };
  assert.equal(parseConfig(env('health', local)).hostname, '127.0.0.1');
  assert.throws(() => parseConfig(env('health', { ...local, LOAD_LOCAL_ACK: '' })));
  for (const port of [80, 443, 3000, 5432, 6379, 8000, 65536]) {
    const url = `http://127.0.0.1:${port}`;
    assert.throws(() => parseConfig(env('health', { ...local, LOAD_BASE_URL: url, LOAD_CONFIRMED_ORIGIN: url })));
  }
});

test('reviewed target IP blocks ambiguous and metadata/multicast addresses', () => {
  for (const ip of ['127.0.0.1', '169.254.169.254', '0.0.0.0', '224.0.0.1', '10.1', '010.1.2.3', '1.2.3.999', '::1']) {
    assert.throws(() => parseConfig(env('health', { LOAD_TARGET_IP: ip })));
  }
  const options = optionsFor(parseConfig(env('health')));
  assert.equal(options.hosts['api.staging.example.test'], '192.0.2.10');
  assert.equal(options.maxRedirects, 0);
  assert.equal(options.insecureSkipTLSVerify, false);
  assert.ok(!options.systemTags.includes('url'));
});

test('ordinary and provider stages are separate and bounded; smoke stays small', () => {
  const ordinary = parseConfig(env('health', { LOAD_PROFILE: 'staged' }));
  assert.deepEqual(ordinary.targets, [10, 50, 100, 250, 500]);
  const heavy = parseConfig(env('chat', { LOAD_PROFILE: 'staged', LOAD_USER_MODE: 'shared' }));
  assert.deepEqual(heavy.targets, [2, 5, 10, 20]);
  assert.deepEqual(parseConfig(env()).targets, [1]);
  assert.equal(optionsFor(ordinary).thresholds.http_req_failed[0], 'rate<0.01');
  assert.equal(optionsFor(heavy).thresholds.request_latency, undefined);
  assert.throws(() => parseConfig(env('chat', { LOAD_P95_MS: '100' })));
  for (const [key, value] of [['LOAD_PAGE_SIZE', '101'], ['LOAD_MAX_PAGES', '101'], ['LOAD_THINK_SECONDS', '0'],
    ['LOAD_RAMP_SECONDS', '500'], ['LOAD_HOLD_SECONDS', '500'], ['LOAD_MAX_REQUESTS', '200001']]) {
    assert.throws(() => parseConfig(env('health', { [key]: value })));
  }
  assert.throws(() => parseConfig(env('search', { LOAD_MAX_REQUESTS: '201' })));
});

test('valid provider search/chat cannot run without separate provider and write opt-ins', () => {
  for (const scenario of ['search', 'chat']) {
    assert.throws(() => parseConfig(env(scenario, { LOAD_PROVIDER_MODE: 'blocked' })));
    assert.throws(() => parseConfig(env(scenario, { LOAD_PROVIDER_GUARD_ACK: '' })));
    assert.throws(() => parseConfig(env(scenario, { LOAD_PROVIDER_MODE: 'real' })));
    assert.equal(parseConfig(env(scenario, { LOAD_PROVIDER_MODE: 'real',
      LOAD_ENABLE_PAID_PROVIDERS: 'I-accept-provider-spending' })).providerMode, 'real');
  }
  assert.throws(() => parseConfig(env('chat', { LOAD_WRITE_ACK: '' })));
  for (const scenario of ['health', 'reads', 'chat-admission', 'upload-admission']) {
    assert.throws(() => parseConfig(env(scenario, { LOAD_PROVIDER_MODE: 'real',
      LOAD_ENABLE_PAID_PROVIDERS: 'I-accept-provider-spending' })));
  }
});

test('credential pool is fixed per VU, requires dedicated users and never falls back to app credentials', () => {
  assert.throws(() => parseConfig(env('reads', { LOAD_CREDENTIALS_ACK: '' })));
  assert.throws(() => parseConfig(env('reads', { LOAD_USERS_JSON: '', DATABASE_URL: SECRET, JWT_SECRET_KEY: SECRET })));
  assert.throws(() => parseConfig(env('reads', { LOAD_USERS_JSON: SECRET })), error => !error.message.includes(SECRET));
  assert.throws(() => parseConfig(env('reads', { LOAD_PROFILE: 'staged' })));
  const user = { token: SECRET, user_id: 1, chat_id: 2, document_id: 3 };
  assert.throws(() => parseConfig(env('reads', { LOAD_USERS_JSON: JSON.stringify([user, user]) })));
  assert.equal(parseConfig(env('reads', { LOAD_PROFILE: 'staged', LOAD_USER_MODE: 'shared' })).users.length, 1);
});

test('launcher strips inherited secrets, proxies and k6 overrides; never exposes tokens in arguments', () => {
  const prepared = prepare('inspect', { ...env(), PATH: process.env.PATH, DATABASE_URL: SECRET,
    REDIS_URL: SECRET, OPENAI_API_KEY: SECRET, HTTP_PROXY: SECRET, K6_HTTP_DEBUG: 'full', K6_OUT: SECRET,
    K6_INSECURE_SKIP_TLS_VERIFY: 'true', K6_CLOUD_TOKEN: SECRET });
  for (const key of ['DATABASE_URL', 'REDIS_URL', 'OPENAI_API_KEY', 'HTTP_PROXY', 'K6_HTTP_DEBUG', 'K6_OUT',
    'K6_INSECURE_SKIP_TLS_VERIFY', 'K6_CLOUD_TOKEN']) assert.equal(prepared.environment[key], undefined);
  assert.ok(prepared.args.includes('--log-output'));
  assert.ok(prepared.args.includes('none'));
  assert.ok(!JSON.stringify(prepared.args).includes(SECRET));
  assert.throws(() => prepare('run', env()));
  assert.throws(() => prepare('run', env(), { example: true }));
  assert.throws(() => prepare('inspect', { ...env(), LOAD_TOOL_MODE: 'run' }));
  const live = prepare('run', { ...env(), LOAD_EXECUTION_ACK: 'run-against-isolated-stack' });
  assert.ok(live.args.includes('--no-usage-report'));
  assert.ok(live.args.includes('--new-machine-readable-summary=false'));
});

test('all seven scenarios pass offline validation; validation does not spawn k6', () => {
  for (const scenario of SCENARIOS) {
    const result = spawnSync(process.execPath, ['load-tests/run.mjs', 'validate', '--example', `--scenario=${scenario}`],
      { cwd: new URL('../../', import.meta.url), encoding: 'utf8', env: { PATH: '' } });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).validation_only, true);
    assert.ok(!result.stdout.includes('synthetic-inspect-token'));
  }
});

test('ordinary reads contain only GETs and cannot trigger providers, uploads, auth exchanges or deletes', () => {
  const r = rig('reads', {}, (_method, url) => url.includes('?limit=') ? response(200, []) : response(200, { id: 1 }));
  for (let index = 0; index < 25; index++) r.traffic.step(1, index);
  assert.ok(r.calls.every(([method]) => method === 'GET'));
  assert.ok(r.calls.every(([, url]) => !/ask|search|logout|google|retry/.test(url)));
  assert.ok(r.calls.some(([, url]) => url.endsWith('/documents/upload-policy')));
  assert.ok(r.calls.every(([, url]) => url.startsWith(r.config.baseURL)));
});

test('pagination follows signed cursors for documents/chats/messages without offsets or URL following', () => {
  let paged = 0;
  const r = rig('reads', {}, (_method, url) => {
    if (!url.includes('?limit=')) return response(200, { id: 1 });
    paged++;
    const older = url.includes('cursor=');
    return response(200, [{ id: older ? 1 : 2, chat_id: 1 }], older ? {} : { 'x-next-cursor': 'signed_cursor_1' });
  });
  for (let index = 0; index < 15; index++) r.traffic.step(1, index);
  assert.equal(paged, 6);
  assert.equal(r.calls.filter(([, url]) => url.includes('cursor=signed_cursor_1')).length, 3);
  assert.ok(r.calls.every(([, url]) => !url.includes('offset=')));
  assert.equal(r.metrics.filter(metric => metric.name === 'pagination_completed').length, 3);
});

test('duplicate page rows, repeated cursors and foreign message ownership fail rather than inventing success', () => {
  for (const variant of ['duplicate', 'cursor', 'foreign']) {
    let id = 0;
    const r = rig('reads', {}, (_method, url) => {
      if (!url.includes('?limit=')) return response(200, {});
      id++;
      return response(200, [{ id: variant === 'duplicate' ? 1 : id, chat_id: variant === 'foreign' ? 999 : 1 }],
        { 'X-Next-Cursor': variant === 'cursor' ? 'repeated' : `cursor_${id}` });
    });
    assert.throws(() => { for (let index = 0; index < 20; index++) r.traffic.step(1, index); }, /contract/);
  }
});

test('page caps are explicitly counted as capped, not fully traversed', () => {
  const r = rig('reads', { LOAD_MAX_PAGES: '1' }, () => response(200, [{ id: 1 }], { 'X-Next-Cursor': 'more' }));
  r.traffic.step(1, 0); r.traffic.step(1, 1); r.traffic.step(1, 2);
  assert.ok(r.metrics.some(metric => metric.name === 'pagination_capped'));
  assert.ok(!r.metrics.some(metric => metric.name === 'pagination_completed'));
});

test('upload admission sends one deliberately unsupported byte and aborts on unexpected acceptance', () => {
  const r = rig('upload-admission', {}, () => response(400, { code: 'unsupported_file' }));
  r.traffic.step(1, 0); r.traffic.step(1, 1);
  const [method, url, body] = r.calls[1];
  assert.equal(method, 'POST'); assert.ok(url.endsWith('/documents'));
  assert.deepEqual(body.file, { content: 'x', filename: 'load-probe.invalid', type: 'application/octet-stream' });
  const accepted = rig('upload-admission', {}, () => response(200, { id: 1 }));
  accepted.traffic.step(1, 0);
  assert.throws(() => accepted.traffic.step(1, 1), /contract/);
});

test('provider-free search/chat probes use only empty inputs and distinguish expected 400 from failures', () => {
  for (const scenario of ['search-admission', 'chat-admission']) {
    const detail = scenario.startsWith('search') ? 'Search query cannot be empty' : 'Question cannot be empty';
    const r = rig(scenario, {}, () => response(400, { detail }));
    r.traffic.step(1, 0); r.traffic.step(1, 1);
    assert.equal(r.calls[1][2][scenario.startsWith('search') ? 'query' : 'question'], '');
    assert.equal(r.metrics.filter(m => m.name === 'unexpected_failures').at(-1).value, false);
    assert.equal(r.metrics.filter(m => m.name === 'raw_failures').at(-1).value, true);
  }
});

test('admission rejection preserves Retry-After and credentials instead of rotating or retrying immediately', () => {
  const r = rig('chat-admission', {}, () => response(429, { code: 'rate_limit' }, { 'Retry-After': '3600', 'X-Resource-Error': 'rate_limit' }));
  r.traffic.step(1, 0); r.traffic.step(1, 1); r.traffic.step(1, 2);
  assert.ok(r.sleeps.includes(3600));
  assert.equal(new Set(r.calls.map(call => call[3].headers.Authorization)).size, 1);
  assert.equal(r.metrics.filter(m => m.name === 'admission_rejections' && m.value).length, 2);
  assert.equal(r.metrics.filter(m => m.name === 'unexpected_failures' && m.value).length, 0);
});

test('ordinary 429 remains a failure; 503 admission outage is never counted as healthy backpressure', () => {
  for (const [scenario, status, code] of [['reads', 429, 'rate_limit'], ['chat-admission', 503, 'admission_unavailable']]) {
    const r = rig(scenario, {}, () => response(status, { code }, { 'Retry-After': '5' }));
    r.traffic.step(1, 0); r.traffic.step(1, 1);
    assert.equal(r.metrics.filter(m => m.name === 'unexpected_failures').at(-1).value, true);
    assert.ok(r.sleeps.includes(5));
  }
});

test('redirects, bad credentials and invalid Retry-After abort without exposing response data', () => {
  for (const result of [response(302, { token: SECRET }, { Location: 'https://production.invalid' }),
    response(401, { token: SECRET }), response(429, { code: 'rate_limit', token: SECRET }, { 'Retry-After': 'invalid' })]) {
    const r = rig('reads', {}, () => result);
    r.traffic.step(1, 0);
    assert.throws(() => r.traffic.step(1, 1), error => !error.message.includes(SECRET));
    assert.ok(!JSON.stringify(r.metrics).includes(SECRET));
  }
});

test('request/VU caps stop before a new request; identity mismatch also stops', () => {
  const r = rig('chat', { LOAD_MAX_REQUESTS: '1' });
  r.traffic.step(1, 0);
  assert.throws(() => r.traffic.step(1, 1), /ceiling/);
  assert.equal(r.calls.length, 1);
  assert.throws(() => r.traffic.step(2, 0), /ceiling/);
  const wrong = rig('reads', { LOAD_USERS_JSON: JSON.stringify([{ token: SECRET, user_id: 99, chat_id: 1, document_id: 1 }]) });
  assert.throws(() => wrong.traffic.step(1, 0), /contract/);
});

test('malformed successful JSON cannot silently pass a read or provider contract', () => {
  for (const scenario of ['reads', 'search', 'chat', 'health']) {
    for (const body of ['null', 'false', '"not an object"', '<html>upstream error</html>']) {
      const r = rig(scenario, {}, () => ({ status: 200, body, headers: {} }));
      if (scenario !== 'health') r.traffic.step(1, 0);
      assert.throws(() => r.traffic.step(1, 1), /contract/);
    }
  }
});

test('network failures are counted without exposing transport errors or retrying within an iteration', () => {
  const r = rig('reads', {}, () => ({ status: 0, body: null, error: SECRET, headers: {} }));
  r.traffic.step(1, 0); r.traffic.step(1, 1);
  assert.equal(r.calls.length, 2);
  assert.equal(r.metrics.filter(m => m.name === 'unexpected_failures').at(-1).value, true);
  assert.ok(!JSON.stringify(r.metrics).includes(SECRET));
});

test('credentials file errors expose neither supplied path nor file content', () => {
  assert.throws(() => prepare('inspect', env('reads', { LOAD_USERS_JSON: '',
    LOAD_USERS_FILE: `private/${SECRET}.json` })), error => !error.message.includes(SECRET));
  assert.throws(() => prepare('inspect', env('reads', { LOAD_USERS_JSON: '',
    LOAD_USERS_FILE: 'config.js' })), /inside load-tests\/private/);
});

test('provider scenario sends only fixed synthetic content to a fixture-owned chat', () => {
  const r = rig('chat', {}, () => response(200, { answer: 'synthetic' }));
  r.traffic.step(1, 0); r.traffic.step(1, 1);
  assert.ok(r.calls[1][1].endsWith('/chats/1/ask'));
  assert.deepEqual(r.calls[1][2].document_ids, [1]);
  assert.equal(r.calls[1][2].allow_general_knowledge, false);
  assert.equal(r.calls[1][3].timeout, '120s');
  assert.ok(!JSON.stringify(r.metrics).includes('synthetic'));
});

test('preflight checks only liveness/readiness and aborts on failed dependencies', () => {
  const calls = [];
  const io = { request: (_method, url) => { calls.push(url); return response(200, { status: url.endsWith('/health') ? 'ok' : 'ready' }); },
    metric() {}, sleep() {}, abort: message => { throw new Error(message); } };
  createTraffic(parseConfig(env('health')), io).setup();
  assert.equal(calls.length, 2);
  io.request = () => response(503, { status: 'not_ready' });
  assert.throws(() => createTraffic(parseConfig(env('health')), io).setup(), /Preflight/);
});

test('offline observations report only supplied aggregates and explicitly identify missing instrumentation', () => {
  const run_id = 'a'.repeat(32);
  const summary = { run_id, duration_ms: 10000 };
  const rows = [{ run_id, elapsed_seconds: 1, db_connections: 8, worker_active: 2 },
    { run_id, elapsed_seconds: 9, db_connections: 12, worker_active: 3 }];
  const result = aggregate(summary, rows);
  assert.deepEqual(result.observed.db_connections, { samples: 2, min: 8, max: 12 });
  assert.ok(result.missing.includes('db_pool_capacity'));
  for (const change of [{ run_id: 'b'.repeat(32) }, { elapsed_seconds: 11 }, { db_connections: NaN },
    { db_connections: -1 }, { token: SECRET }, { query: SECRET }]) {
    assert.throws(() => aggregate(summary, [{ ...rows[0], ...change }]), error => !error.message.includes(SECRET));
  }
});

test('k6 adapter has local imports only and suppresses payload-bearing output', () => {
  const source = fs.readFileSync(new URL('../scenarios.js', import.meta.url), 'utf8');
  assert.ok(!/https?:\/\/|console\.(log|error)|\.cookies|\.error_code/.test(source));
  assert.ok(source.includes('requireExecution();'));
  assert.ok(source.includes('responseCallback: http.expectedStatuses'));
  assert.ok(source.includes('redirects: 0'));
});

test('summary retains throughput, percentiles and threshold results without raw data or credentials', () => {
  const config = parseConfig(env('reads', { LOAD_P95_MS: '500' }));
  const summary = buildSummary({ state: { testRunDurationMs: 10000, token: SECRET },
    options: { token: SECRET }, metrics: {
      http_reqs: { values: { count: 100, rate: 10, token: SECRET } },
      request_latency: { values: { avg: 12, 'p(90)': 20, 'p(95)': 30, 'p(99)': 40, max: 50 },
        thresholds: { 'p(95)<500': { ok: true, detail: SECRET }, [SECRET]: { ok: false } } },
      raw_failures: { values: { rate: 0.05, passes: 5, fails: 95 } },
      [SECRET]: { values: { count: 1 } },
    } }, config, 'a'.repeat(32), ['request_latency', 'raw_failures']);
  assert.deepEqual(summary.metrics.http_reqs.values, { count: 100, rate: 10 });
  assert.equal(summary.metrics.request_latency.values['p(99)'], 40);
  assert.deepEqual(summary.metrics.request_latency.thresholds, { 'p(95)<500': { ok: true } });
  assert.equal(summary.metrics.raw_failures.values.rate, 0.05);
  const output = JSON.stringify(summary);
  for (const excluded of [SECRET, config.baseURL, config.ip, config.users[0].token]) assert.ok(!output.includes(excluded));
  assert.throws(() => buildSummary({ metrics: {} }, config, 'a'.repeat(32), []), /format/);
  assert.throws(() => buildSummary({ state: { testRunDurationMs: 1 }, metrics: {} }, config, SECRET, []), /identifier/);
});
