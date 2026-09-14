// Shared by Node's offline validator and k6; no network or filesystem access.
export const SCENARIOS = ['health', 'reads', 'search-admission', 'upload-admission', 'chat-admission', 'search', 'chat'];
const fail = message => { throw new Error(message); };
const ACK = 'dedicated-non-production-stack';

function integer(value, fallback, minimum, maximum, name) {
  if (value === undefined || value === '') return fallback;
  if (!/^[0-9]+$/.test(value)) fail(`Invalid ${name}`);
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < minimum || result > maximum) fail(`Invalid ${name}`);
  return result;
}

function ipv4(value) {
  if (typeof value !== 'string' || !/^(0|[1-9]\d{0,2})(\.(0|[1-9]\d{0,2})){3}$/.test(value)) return false;
  return value.split('.').every(part => Number(part) <= 255);
}

export function targetConfig(env) {
  if (env.LOAD_TARGET_ACK !== ACK) fail('LOAD_TARGET_ACK must confirm a dedicated non-production stack');
  // Intentionally accept origins only, ASCII DNS names or canonical IPv4.
  // No credentials, paths, query/fragment, IPv6 aliases, short/hex IPs or redirects.
  const match = /^(https?):\/\/([a-z0-9.-]+)(?::([1-9][0-9]{0,4}))?$/.exec(env.LOAD_BASE_URL || '');
  if (!match) fail('LOAD_BASE_URL must be an explicit canonical origin without a trailing slash');
  const [, scheme, hostname, portText] = match;
  const port = Number(portText || (scheme === 'https' ? 443 : 80));
  if (port > 65535 || hostname !== env.LOAD_ALLOWED_HOST) fail('Target host/port does not match the explicit allowlist');
  if (env.LOAD_CONFIRMED_ORIGIN !== env.LOAD_BASE_URL) fail('LOAD_CONFIRMED_ORIGIN must exactly match the reviewed target');
  if (!ipv4(env.LOAD_TARGET_IP)) fail('LOAD_TARGET_IP must be a reviewed canonical IPv4 address');
  if (env.LOAD_ENVIRONMENT === 'isolated-local') {
    if (hostname !== '127.0.0.1' || env.LOAD_TARGET_IP !== hostname || !portText || port < 1024 ||
        [3000, 5432, 6379, 8000].includes(port) || env.LOAD_LOCAL_ACK !== 'dedicated-stack-no-production-tunnel') {
      fail('Local targets require 127.0.0.1, a dedicated non-default port and the no-tunnel acknowledgement');
    }
  } else if (env.LOAD_ENVIRONMENT === 'staging') {
    const labels = hostname.split(/[.-]/);
    if (scheme !== 'https' || !hostname.includes('.') || hostname.length > 253 || !/^[a-z]/.test(hostname) ||
        hostname.split('.').some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
        !labels.some(label => ['staging', 'stage', 'loadtest', 'test'].includes(label)) ||
        labels.some(label => /^(prod|production|live|www|localhost|local)[0-9]*$/.test(label))) {
      fail('Staging requires HTTPS and an explicit staging/test hostname without production labels');
    }
    const [a, b] = env.LOAD_TARGET_IP.split('.').map(Number);
    if (a === 0 || a === 127 || a >= 224 || (a === 169 && b === 254)) fail('Unsafe staging IP');
  } else fail('LOAD_ENVIRONMENT must be staging or isolated-local');
  return { baseURL: env.LOAD_BASE_URL, hostname, ip: env.LOAD_TARGET_IP, environment: env.LOAD_ENVIRONMENT };
}

export function parseConfig(env, credentialsText) {
  const target = targetConfig(env);
  const scenario = env.LOAD_SCENARIO;
  if (!SCENARIOS.includes(scenario)) fail('Choose an explicit LOAD_SCENARIO');
  const profile = env.LOAD_PROFILE || 'smoke';
  if (!['smoke', 'staged'].includes(profile)) fail('Invalid LOAD_PROFILE');
  const heavy = ['search', 'chat'].includes(scenario);
  const admission = scenario.endsWith('-admission');
  const ordinary = ['health', 'reads'].includes(scenario);
  const providerMode = env.LOAD_PROVIDER_MODE || 'blocked';
  if (!['blocked', 'mock', 'real'].includes(providerMode)) fail('Invalid LOAD_PROVIDER_MODE');
  if (heavy && providerMode === 'blocked') fail('Provider scenarios require explicit mock or real mode');
  if ((heavy || admission) && env.LOAD_PROVIDER_GUARD_ACK !== 'isolated-egress-and-fixtures-reviewed') {
    fail('Review the isolated provider egress/fixtures before any admission or provider scenario');
  }
  if (providerMode === 'real' && (!heavy || env.LOAD_ENABLE_PAID_PROVIDERS !== 'I-accept-provider-spending')) {
    fail('Real providers require a separate explicit spending acknowledgement on search/chat only');
  }
  if (scenario === 'chat' && env.LOAD_WRITE_ACK !== 'synthetic-chat-messages-only') {
    fail('Chat requires acknowledgement of bounded synthetic message creation');
  }
  const targets = profile === 'smoke' ? [1] : ordinary ? [10, 50, 100, 250, 500] : [2, 5, 10, 20];
  const maxVUs = Math.max(...targets);
  const rampSeconds = integer(env.LOAD_RAMP_SECONDS, 30, 10, 120, 'LOAD_RAMP_SECONDS');
  const holdSeconds = integer(env.LOAD_HOLD_SECONDS, 30, 10, 120, 'LOAD_HOLD_SECONDS');
  const thinkSeconds = integer(env.LOAD_THINK_SECONDS, heavy ? 10 : 1, 1, 60, 'LOAD_THINK_SECONDS');
  const pageSize = integer(env.LOAD_PAGE_SIZE, 50, 1, 100, 'LOAD_PAGE_SIZE');
  const maxPages = integer(env.LOAD_MAX_PAGES, 10, 1, 100, 'LOAD_MAX_PAGES');
  const maxRequests = integer(env.LOAD_MAX_REQUESTS, heavy ? 20 : admission ? 100 : 10000,
                              1, heavy ? 200 : admission ? 1000 : 200000, 'LOAD_MAX_REQUESTS');
  const userMode = env.LOAD_USER_MODE || 'per-vu';
  if (!['per-vu', 'shared'].includes(userMode)) fail('Invalid LOAD_USER_MODE');
  let users = [];
  if (scenario !== 'health') {
    if (env.LOAD_CREDENTIALS_ACK !== 'dedicated-test-accounts-no-production-tokens') fail('Dedicated test credentials must be acknowledged');
    try { users = JSON.parse(credentialsText || env.LOAD_USERS_JSON || ''); }
    catch { fail('Invalid load-test credentials JSON'); }
    if (!Array.isArray(users) || !users.length || users.length > 500) fail('Provide 1 to 500 dedicated test users');
    if (users.some(user => !user || typeof user.token !== 'string' ||
        !/^[A-Za-z0-9_.~-]{16,4096}$/.test(user.token) ||
        ['user_id', 'chat_id', 'document_id'].some(key => !Number.isSafeInteger(user[key]) || user[key] <= 0))) {
      fail('Each fixture requires a safe bearer token and positive user_id/chat_id/document_id');
    }
    if (new Set(users.map(user => user.token)).size !== users.length || new Set(users.map(user => user.user_id)).size !== users.length) {
      fail('Duplicate test identities are not allowed; select shared mode explicitly');
    }
    if (userMode === 'per-vu' && users.length < maxVUs) fail('Per-VU mode requires a distinct fixture account for every peak VU');
    if (userMode === 'shared' && users.length !== 1) fail('Shared mode requires exactly one fixed account; no rotation after rejection');
    users = users.map(({ token, user_id, chat_id, document_id }) => ({ token, user_id, chat_id, document_id }));
  }
  let p95 = null;
  if (env.LOAD_P95_MS) {
    if (!ordinary) fail('Latency gates are supported only for ordinary traffic');
    p95 = integer(env.LOAD_P95_MS, null, 1, 600000, 'LOAD_P95_MS');
  }
  return { ...target, scenario, profile, providerMode, heavy, admission, ordinary, targets, maxVUs,
    rampSeconds, holdSeconds, thinkSeconds, pageSize, maxPages, maxRequests, userMode, users, p95 };
}

export function optionsFor(config) {
  const stages = config.profile === 'smoke' ? [{ duration: '10s', target: 1 }] : config.targets.flatMap(target => [
    { duration: `${config.rampSeconds}s`, target }, { duration: `${config.holdSeconds}s`, target },
  ]);
  stages.push({ duration: '10s', target: 0 });
  const thresholds = { contract_failures: ['rate==0'], unexpected_failures: ['rate<0.01'] };
  if (config.ordinary) thresholds.http_req_failed = ['rate<0.01'];
  if (config.p95) thresholds.request_latency = [`p(95)<${config.p95}`];
  return {
    scenarios: { workload: { executor: 'ramping-vus', startVUs: config.profile === 'smoke' ? 1 : 0,
      stages, gracefulRampDown: config.heavy ? '120s' : '30s', gracefulStop: config.heavy ? '120s' : '30s' } },
    thresholds, summaryTrendStats: ['avg', 'p(90)', 'p(95)', 'p(99)', 'max'],
    hosts: { [config.hostname]: config.ip }, maxRedirects: 0, insecureSkipTLSVerify: false,
    discardResponseBodies: true, systemTags: ['scenario', 'name', 'method', 'status', 'expected_response'],
  };
}

export function describe(config) {
  return { scenario: config.scenario, profile: config.profile, environment: config.environment,
    provider_mode: config.providerMode, targets: config.targets, max_requests: config.maxRequests,
    fixture_accounts: config.users.length, user_mode: config.userMode, page_size: config.pageSize,
    max_pages: config.maxPages, think_seconds: config.thinkSeconds };
}
