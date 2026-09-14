import { describe, optionsFor } from './config.js';

const VALUES = new Set(['count', 'rate', 'value', 'min', 'max', 'avg', 'p(90)', 'p(95)', 'p(99)', 'passes', 'fails']);
const BUILT_INS = ['http_reqs', 'http_req_failed', 'http_req_duration', 'http_req_waiting',
  'http_req_blocked', 'http_req_connecting', 'iterations', 'iteration_duration', 'vus', 'vus_max'];

export function buildSummary(data, config, runId, customMetricNames) {
  if (!/^[a-f0-9]{32}$/.test(runId || '')) throw new Error('Missing safe run identifier');
  // The launcher explicitly selects the legacy machine-readable shape. Fail
  // visibly on incompatible versions instead of emitting an empty success report.
  if (!Number.isFinite(data.state?.testRunDurationMs) || data.state.testRunDurationMs < 0 ||
      !data.metrics || typeof data.metrics !== 'object') throw new Error('Unsupported k6 summary format');
  const allowed = new Set([...customMetricNames, ...BUILT_INS]);
  const expectedThresholds = optionsFor(config).thresholds;
  const selected = {};
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (!allowed.has(name)) continue;
    if (!metric?.values || typeof metric.values !== 'object') throw new Error('Unsupported k6 metric format');
    const values = Object.fromEntries(Object.entries(metric.values).filter(([key, value]) =>
      VALUES.has(key) && Number.isFinite(value)));
    const thresholds = {};
    for (const expression of expectedThresholds[name] || []) {
      const ok = metric.thresholds?.[expression]?.ok;
      if (typeof ok === 'boolean') thresholds[expression] = { ok };
    }
    selected[name] = { values, thresholds };
  }
  return { run_id: runId, configuration: describe(config),
    duration_ms: data.state.testRunDurationMs, metrics: selected };
}
