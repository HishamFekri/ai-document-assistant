// Offline aggregation only. Never connects to databases, Redis or worker APIs.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const FIELDS = ['db_connections', 'db_active', 'db_waiting', 'db_max_connections',
  'db_pool_checked_out', 'db_pool_capacity', 'redis_connected_clients', 'redis_rejected_connections',
  'redis_used_memory_bytes', 'worker_active', 'worker_reserved', 'worker_concurrency', 'queue_depth'];

export function aggregate(summary, rows) {
  if (!/^[a-f0-9]{32}$/.test(summary.run_id || '') || !Number.isFinite(summary.duration_ms) || summary.duration_ms <= 0) {
    throw new Error('A real run summary with duration and run ID is required');
  }
  if (!Array.isArray(rows) || !rows.length || rows.length > 10000) throw new Error('Provide 1 to 10000 observation samples');
  const samples = Object.fromEntries(FIELDS.map(name => [name, []]));
  for (const row of rows) {
    if (row.run_id !== summary.run_id || !Number.isFinite(row.elapsed_seconds) || row.elapsed_seconds < 0 ||
        row.elapsed_seconds > summary.duration_ms / 1000 ||
        Object.keys(row).some(key => !['run_id', 'elapsed_seconds', ...FIELDS].includes(key))) {
      throw new Error('Observation fields, run ID or sample time do not match the run');
    }
    for (const field of FIELDS) {
      if (row[field] === undefined) continue;
      if (!Number.isSafeInteger(row[field]) || row[field] < 0) throw new Error('Observations must be nonnegative integer aggregates');
      samples[field].push(row[field]);
    }
  }
  return { run_id: summary.run_id, observation_samples: rows.length,
    observed: Object.fromEntries(Object.entries(samples).filter(([, values]) => values.length).map(([name, values]) => [
      name, { samples: values.length, min: Math.min(...values), max: Math.max(...values) },
    ])), missing: FIELDS.filter(name => !samples[name].length) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 4) throw new Error();
    const [summaryFile, samplesFile] = process.argv.slice(2);
    for (const file of [summaryFile, samplesFile]) if (fs.statSync(file).size > 2 * 1024 * 1024) throw new Error();
    const result = aggregate(JSON.parse(fs.readFileSync(summaryFile, 'utf8')), JSON.parse(fs.readFileSync(samplesFile, 'utf8')));
    console.log(JSON.stringify(result, null, 2));
  } catch { console.error('Invalid local summary/observations; no network access was attempted'); process.exitCode = 1; }
}
