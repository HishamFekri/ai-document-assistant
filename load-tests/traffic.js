// Transport-independent scenario logic. Offline tests supply an in-memory transport.
const admissionCodes = new Set(['rate_limit', 'concurrency_limit', 'processing_quota', 'document_quota', 'storage_quota']);
const json = response => { try { return JSON.parse(response.body); } catch { return null; } };
const header = (response, name) => {
  const key = Object.keys(response.headers || {}).find(key => key.toLowerCase() === name.toLowerCase());
  return key ? response.headers[key] : '';
};

export function createTraffic(config, io) {
  const state = { verified: false, turn: 0, pages: {} };
  let lastResponse;
  function violation() {
    io.metric('contract_failures', true);
    io.abort('Response contract failed; stopping without logging response data');
  }
  function request(method, path, name, user, body = null, admissionProbe = false) {
    // These are the only network entry points; paths are constructed internally.
    const response = io.request(method, config.baseURL + path, body, {
      headers: user ? { Authorization: `Bearer ${user.token}` } : {},
      name, timeout: config.heavy ? '120s' : '20s', admissionProbe,
      acceptsRejection: config.admission || config.heavy,
    });
    lastResponse = response;
    const status = response.status || 0;
    const parsed = json(response);
    const code = header(response, 'X-Resource-Error') || parsed?.code;
    const rejected = status === 429 && admissionCodes.has(code);
    const unavailable = status === 503 && code === 'admission_unavailable';
    const expectedProbe = admissionProbe && status === 400 && (
      (config.scenario === 'upload-admission' && parsed?.code === 'unsupported_file') ||
      (config.scenario === 'search-admission' && parsed?.detail === 'Search query cannot be empty') ||
      (config.scenario === 'chat-admission' && parsed?.detail === 'Question cannot be empty'));
    const success = !admissionProbe && status >= 200 && status < 300;
    io.metric('requests', 1, { endpoint: name });
    io.metric('request_latency', response.timings?.duration || 0, { endpoint: name });
    io.metric('raw_failures', status < 200 || status >= 300, { endpoint: name });
    io.metric('admission_rejections', rejected, { endpoint: name });
    io.metric('admission_unavailable', unavailable, { endpoint: name });
    io.metric('unexpected_failures', !(success || expectedProbe || (rejected && !config.ordinary)), { endpoint: name });
    io.metric('contract_failures', false);
    if (rejected || unavailable) {
      io.metric('admission_codes', 1, { code });
      const delayText = String(header(response, 'Retry-After'));
      if (!/^[1-9][0-9]{0,4}$/.test(delayText) || Number(delayText) > 86400) violation();
      io.metric('retry_after_seconds', Number(delayText));
      // Never clamp and retry early; k6's scenario deadline can interrupt this sleep.
      io.sleep(Number(delayText));
      return null;
    }
    if ([401, 403].includes(status)) io.abort('Test identity rejected; no credential rotation or login retries');
    if (status >= 300 && status < 400) io.abort('Redirect refused; target must be the final reviewed origin');
    if (admissionProbe && !expectedProbe) violation();
    if (success && (!parsed || typeof parsed !== 'object')) violation();
    return success ? parsed : null;
  }
  function page(resource, path, user) {
    const chain = state.pages[resource] || { cursor: '', seen: new Set(), count: 0 };
    const query = `?limit=${config.pageSize}` + (chain.cursor ? `&cursor=${encodeURIComponent(chain.cursor)}` : '');
    // Read only this response's cursor; never follow URLs from response bodies.
    const rows = request('GET', path + query, resource, user);
    if (rows === null) return;
    if (!Array.isArray(rows) || rows.length > config.pageSize) violation();
    for (const row of rows) {
      if (!Number.isSafeInteger(row?.id) || row.id <= 0) violation();
      if (chain.seen.has(row.id)) { io.metric('pagination_duplicates', 1); violation(); }
      chain.seen.add(row.id);
      if (resource === 'messages' && row.chat_id !== user.chat_id) violation();
    }
    const cursor = header(lastResponse, 'X-Next-Cursor');
    if (cursor && (!/^[A-Za-z0-9_-]{1,2048}$/.test(cursor) || cursor === chain.cursor || !rows.length)) violation();
    chain.count++;
    io.metric('pagination_pages', 1, { endpoint: resource });
    if (!cursor || chain.count >= config.maxPages) {
      io.metric(cursor ? 'pagination_capped' : 'pagination_completed', 1, { endpoint: resource });
      delete state.pages[resource];
    } else state.pages[resource] = { ...chain, cursor };
  }
  return {
    setup() {
      const health = request('GET', '/health', 'preflight_health', null);
      const ready = request('GET', '/ready', 'preflight_ready', null);
      if (health?.status !== 'ok' || ready?.status !== 'ready') io.abort('Preflight liveness/readiness failed');
    },
    step(vu, iteration) {
      if (vu > config.maxVUs || vu < 1) io.abort('Configured VU ceiling exceeded');
      // k6 iterationInTest is unique across VUs. One request per iteration, no
      // retries within an iteration. The two preflight reads are additional.
      if (iteration >= config.maxRequests) io.abort('Workload request ceiling reached; controlled stop');
      const user = config.users[config.userMode === 'shared' ? 0 : vu - 1];
      if (config.scenario !== 'health' && !state.verified) {
        const identity = request('GET', '/auth/me', 'identity', user);
        if (identity) {
          if (identity.id !== user.user_id) violation();
          state.verified = true;
        }
      } else if (config.scenario === 'health') {
        const name = state.turn++ % 2 ? 'readiness' : 'liveness';
        const response = request('GET', name === 'readiness' ? '/ready' : '/health', name, null);
        if (response && response.status !== (name === 'readiness' ? 'ready' : 'ok')) violation();
      } else if (config.scenario === 'reads') {
        const action = state.turn++ % 7;
        if (action === 0) request('GET', '/auth/me', 'auth_me', user);
        if (action === 1) page('documents', '/documents', user);
        if (action === 2) page('chats', '/chats', user);
        if (action === 3) page('messages', `/chats/${user.chat_id}/messages`, user);
        if (action === 4) request('GET', `/documents/${user.document_id}`, 'document_detail', user);
        if (action === 5) request('GET', `/chats/${user.chat_id}`, 'chat_detail', user);
        if (action === 6) request('GET', '/documents/upload-policy', 'upload_policy', user);
      } else if (config.scenario === 'upload-admission') {
        // One byte, deliberately unsupported suffix: rejected before saving or dispatch.
        request('POST', '/documents', 'upload_admission', user,
                { file: io.file('x', 'load-probe.invalid', 'application/octet-stream') }, true);
      } else if (config.scenario.startsWith('search')) {
        const probe = config.admission;
        const result = request('POST', `/chats/${user.chat_id}/search`, probe ? 'search_admission' : 'search', user,
                               { query: probe ? '' : 'What colour is the synthetic test marker?' }, probe);
        if (result && !Array.isArray(result)) violation();
      } else {
        const result = request('POST', `/chats/${user.chat_id}/ask`, config.admission ? 'chat_admission' : 'chat', user,
          { question: config.admission ? '' : 'What colour is the synthetic test marker?',
            document_ids: [user.document_id], allow_general_knowledge: false }, config.admission);
        if (result && typeof result.answer !== 'string') violation();
      }
      io.sleep(config.thinkSeconds);
    },
  };
}
