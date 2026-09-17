import http from 'k6/http';
import { sleep } from 'k6';
import execution from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';
import { parseConfig, optionsFor } from './config.js';
import { createTraffic } from './traffic.js';
import { buildSummary } from './summary.js';

if (!['inspect', 'run'].includes(__ENV.LOAD_TOOL_MODE)) throw new Error('Use the guarded load-tests/run.mjs launcher');
const credentials = __ENV.LOAD_USERS_FILE ? open(__ENV.LOAD_USERS_FILE) : undefined;
const config = parseConfig(__ENV, credentials);
export const options = optionsFor(config);
const metrics = {
  requests: new Counter('requests'), request_latency: new Trend('request_latency', true),
  raw_failures: new Rate('raw_failures'), unexpected_failures: new Rate('unexpected_failures'),
  contract_failures: new Rate('contract_failures'), admission_rejections: new Rate('admission_rejections'),
  admission_unavailable: new Rate('admission_unavailable'), admission_codes: new Counter('admission_codes'),
  retry_after_seconds: new Trend('retry_after_seconds'), pagination_pages: new Counter('pagination_pages'),
  pagination_completed: new Counter('pagination_completed'), pagination_capped: new Counter('pagination_capped'),
  pagination_duplicates: new Counter('pagination_duplicates'),
};
const traffic = createTraffic(config, {
  sleep, abort: message => execution.test.abort(message), file: http.file,
  metric: (name, value, tags = {}) => metrics[name].add(value, tags),
  request(method, url, body, settings) {
    const multipart = body?.file !== undefined;
    const headers = { ...settings.headers };
    if (body && !multipart) headers['Content-Type'] = 'application/json';
    const expected = settings.admissionProbe ? [400, 429] : settings.acceptsRejection ? [200, 429] : [200];
    return http.request(method, url, body && !multipart ? JSON.stringify(body) : body, {
      headers, redirects: 0, timeout: settings.timeout, responseType: 'text',
      tags: { name: settings.name }, responseCallback: http.expectedStatuses(...expected),
    });
  },
});

function requireExecution() {
  if (__ENV.LOAD_TOOL_MODE !== 'run' || __ENV.LOAD_EXECUTION_ACK !== 'run-against-isolated-stack') {
    throw new Error('Live execution is disabled; inspection performs no requests');
  }
}
export function setup() { requireExecution(); traffic.setup(); }
export default function () {
  requireExecution();
  traffic.step(execution.vu.idInTest, execution.scenario.iterationInTest);
}
export function handleSummary(data) {
  const summary = buildSummary(data, config, __ENV.LOAD_RUN_ID, Object.keys(metrics));
  // Aggregate values only: no request/response bodies, URLs, cookies or tokens.
  const output = JSON.stringify(summary, null, 2) + '\n';
  return { stdout: output, [`results/${__ENV.LOAD_RUN_ID}.json`]: output };
}
