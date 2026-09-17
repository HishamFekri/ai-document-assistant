#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { parseConfig, describe, SCENARIOS } from './config.js';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const INPUTS = new Set(['LOAD_BASE_URL', 'LOAD_ALLOWED_HOST', 'LOAD_TARGET_IP', 'LOAD_TARGET_ACK',
  'LOAD_CONFIRMED_ORIGIN', 'LOAD_ENVIRONMENT', 'LOAD_LOCAL_ACK', 'LOAD_SCENARIO', 'LOAD_PROFILE',
  'LOAD_RAMP_SECONDS', 'LOAD_HOLD_SECONDS', 'LOAD_THINK_SECONDS', 'LOAD_PAGE_SIZE', 'LOAD_MAX_PAGES',
  'LOAD_MAX_REQUESTS', 'LOAD_USER_MODE', 'LOAD_USERS_JSON', 'LOAD_USERS_FILE', 'LOAD_CREDENTIALS_ACK',
  'LOAD_PROVIDER_MODE', 'LOAD_PROVIDER_GUARD_ACK', 'LOAD_ENABLE_PAID_PROVIDERS', 'LOAD_WRITE_ACK',
  'LOAD_EXECUTION_ACK', 'LOAD_P95_MS']);
const SYSTEM = new Set(['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL']);

export function exampleEnvironment(scenario = 'reads') {
  return {
    LOAD_BASE_URL: 'https://api.staging.example.test', LOAD_CONFIRMED_ORIGIN: 'https://api.staging.example.test',
    LOAD_ALLOWED_HOST: 'api.staging.example.test', LOAD_TARGET_IP: '192.0.2.10', LOAD_ENVIRONMENT: 'staging',
    LOAD_TARGET_ACK: 'dedicated-non-production-stack', LOAD_SCENARIO: scenario, LOAD_PROFILE: 'smoke',
    LOAD_PROVIDER_MODE: 'mock', LOAD_PROVIDER_GUARD_ACK: 'isolated-egress-and-fixtures-reviewed',
    LOAD_CREDENTIALS_ACK: 'dedicated-test-accounts-no-production-tokens',
    LOAD_WRITE_ACK: 'synthetic-chat-messages-only',
    LOAD_USERS_JSON: JSON.stringify([{ token: 'synthetic-inspect-token-only', user_id: 1, chat_id: 1, document_id: 1 }]),
  };
}

export function prepare(command, source, { example = false, scenario } = {}) {
  if (!['validate', 'inspect', 'run'].includes(command)) throw new Error('Choose validate, inspect or run');
  if (example && command === 'run') throw new Error('Example configuration can never execute load traffic');
  const input = example ? exampleEnvironment(scenario) : source;
  if (scenario && !SCENARIOS.includes(scenario)) throw new Error('Unknown scenario');
  const environment = {};
  for (const [name, value] of Object.entries(source)) {
    if (SYSTEM.has(name.toUpperCase())) environment[name] = value;
  }
  for (const [name, value] of Object.entries(input)) {
    if (name.startsWith('LOAD_') && !INPUTS.has(name)) throw new Error('Unknown LOAD_ setting; launcher overrides are forbidden');
    if (INPUTS.has(name)) environment[name] = value;
  }
  if (scenario) environment.LOAD_SCENARIO = scenario;
  let credentials;
  if (environment.LOAD_USERS_FILE) {
    if (environment.LOAD_USERS_JSON) throw new Error('Choose one credentials source');
    const filename = path.resolve(ROOT, environment.LOAD_USERS_FILE);
    const privateRoot = path.join(ROOT, 'private') + path.sep;
    let actual, size;
    try { actual = fs.realpathSync(filename); size = fs.statSync(actual).size; }
    catch { throw new Error('Cannot read the private test credentials file'); }
    if (!actual.startsWith(privateRoot) || path.extname(actual) !== '.json' || size > 1024 * 1024) {
      throw new Error('Credentials must be a JSON file inside load-tests/private, at most 1 MiB');
    }
    try { credentials = fs.readFileSync(actual, 'utf8'); }
    catch { throw new Error('Cannot read the private test credentials file'); }
    environment.LOAD_USERS_FILE = './' + path.relative(ROOT, actual).split(path.sep).join('/');
  }
  const config = parseConfig(environment, credentials);
  if (command === 'run' && environment.LOAD_EXECUTION_ACK !== 'run-against-isolated-stack') {
    throw new Error('Live execution requires LOAD_EXECUTION_ACK=run-against-isolated-stack');
  }
  environment.LOAD_TOOL_MODE = command === 'run' ? 'run' : 'inspect';
  environment.LOAD_RUN_ID = randomUUID().replaceAll('-', '');
  // No inherited K6 settings, proxies, exporters, cloud tokens, DB URLs or provider
  // credentials. Supply a known empty config instead of the user's k6 config.
  const args = ['--config', path.join(ROOT, 'k6.json'), '--log-output', 'none', '--quiet', command,
                '--include-system-env-vars'];
  if (command === 'run') args.push('--no-usage-report', '--new-machine-readable-summary=false');
  if (command === 'inspect') args.push('--execution-requirements');
  args.push(path.join(ROOT, 'scenarios.js'));
  return { environment, config, args };
}

export function main(argv = process.argv.slice(2)) {
  const [command, ...flags] = argv;
  if (flags.some(flag => flag !== '--example' && !flag.startsWith('--scenario='))) throw new Error('Unsupported launcher argument');
  if (flags.filter(flag => flag.startsWith('--scenario=')).length > 1) throw new Error('Choose one scenario per run');
  const scenario = flags.find(flag => flag.startsWith('--scenario='))?.slice('--scenario='.length);
  const prepared = prepare(command, process.env, { example: flags.includes('--example'), scenario });
  if (command === 'validate') {
    console.log(JSON.stringify({ validation_only: true, ...describe(prepared.config) }, null, 2));
    return 0;
  }
  if (command === 'run') {
    try { fs.mkdirSync(path.join(ROOT, 'results'), { recursive: true }); }
    catch { throw new Error('Cannot create the local results directory'); }
    console.log(JSON.stringify({ run_id: prepared.environment.LOAD_RUN_ID, ...describe(prepared.config) }));
  }
  const result = spawnSync('k6', prepared.args, { cwd: ROOT, env: prepared.environment, stdio: 'inherit' });
  if (result.error) throw new Error('Could not launch k6; install it locally and check PATH');
  return result.status ?? 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = main(); }
  catch (error) {
    // Our own fixed configuration messages only; never print stack or env values.
    console.error(error.message);
    process.exitCode = 1;
  }
}
