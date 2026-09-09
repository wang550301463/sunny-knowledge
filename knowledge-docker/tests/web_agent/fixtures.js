import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { test, expect, login, publicURL, workerID } from '../web/fixtures.js';

export { test, expect, publicURL };
const artifactRoot = '/artifacts/web-agent';

export function record(name, value) {
  mkdirSync(artifactRoot, { recursive: true });
  writeFileSync(`${artifactRoot}/${name}.json`, JSON.stringify(value, null, 2) + '\n');
}

export async function realLogin(page) {
  const observed = { pkce: false, authorizationCodeExchange: false };
  const observe = request => {
    const url = new URL(request.url());
    if (url.pathname.endsWith('/protocol/openid-connect/auth')) {
      observed.pkce = url.searchParams.get('code_challenge_method') === 'S256'
        && url.searchParams.get('response_type') === 'code'
        && url.searchParams.get('client_id') === 'knowledge-web'
        && (url.searchParams.get('code_challenge')?.length ?? 0) >= 43;
    }
    if (url.pathname.endsWith('/protocol/openid-connect/token')) {
      const data = new URLSearchParams(request.postData() ?? '');
      observed.authorizationCodeExchange = data.get('grant_type') === 'authorization_code'
        && (data.get('code_verifier')?.length ?? 0) >= 43;
    }
  };
  page.on('request', observe);
  try { await login(page); }
  finally { page.off('request', observe); }
  expect(observed).toEqual({ pkce: true, authorizationCodeExchange: true });
  return observed;
}

// Setup and assertions use the actual current browser's bearer, never a static admin token.
// Only selected response bodies return to the runner; no credentials are exported.
export async function request(page, method, path, body) {
  return page.evaluate(async ({ method, path, body }) => {
    const key = Object.keys(sessionStorage).find(k => k.startsWith('knowledge.session.user:'));
    if (!key) return { status: 401, data: null };
    const user = JSON.parse(sessionStorage.getItem(key));
    const response = await fetch('/api/v1' + path, {
      method, credentials: 'omit', cache: 'no-store', redirect: 'error',
      headers: { Authorization: 'Bearer ' + user.access_token, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    const contentType = response.headers.get('content-type') ?? '';
    return { status: response.status, data: contentType.includes('application/json') ? JSON.parse(text) : text };
  }, { method, path, body });
}

export async function api(page, method, path, body, status = 200) {
  const response = await request(page, method, path, body);
  expect(response.status, `${method} ${path}: actual authorized API status`).toBe(status);
  return response.data;
}

export function assets() {
  const suffix = randomUUID().replaceAll('-', '');
  return { suffix, name: '浏览器智能体验收-' + suffix.slice(0, 8), marker: 'browser-evidence-' + suffix };
}

export async function seed(page, owned) {
  const me = await api(page, 'GET', '/me');
  owned.user = 'user:' + me.id;
  owned.worker = 'service:' + workerID;
  owned.space = await api(page, 'POST', '/spaces', { name: owned.name }, 201);
  for (const action of ['read', 'write'])
    await api(page, 'PUT', '/grants', { space_id: owned.space.id, action, subjects: [owned.user, owned.worker] });
  owned.source = await api(page, 'POST', '/sources', {
    name: owned.marker, kind: 'markdown', space_id: owned.space.id,
    config: { path: 'docs/browser-agent.md', content: `# ${owned.marker}\r\n发布必须保留不可变修订与原始引用。\r\n` },
  }, 201);
  await api(page, 'POST', `/sources/${owned.source.id}/preview`, { base_version: 1 });
  let task = await api(page, 'POST', `/sources/${owned.source.id}/sync`, { base_version: 1 }, 202);
  await expect.poll(async () => {
    task = await api(page, 'GET', `/tasks/${task.id}`);
    return task.status;
  }, { timeout: 100_000, intervals: [250, 500, 1000] }).toBe('review_needed');
  owned.page = task.result.items[0];
  owned.revision = await api(page, 'POST', `/reviews/${owned.page.proposal_id}/approve`, {
    reason: 'Approve isolated deterministic browser protocol fixture after source verification',
  });
  owned.model = await api(page, 'POST', '/models', {
    name: owned.name + ' Chat protocol fixture', provider: 'openai',
    provider_model: 'protocol-fixture-chat', capability: 'chat',
    base_url: 'http://agent-model-provider:8080/v1', max_input_chars: 100000,
    max_output_tokens: 2048, timeout_seconds: 15, max_retries: 0,
  }, 201);
  const tested = await api(page, 'POST', `/models/${owned.model.id}/test`, {
    configuration_id: owned.model.configuration_id, test_tools: true, test_stream: true,
  });
  expect(tested.test_state).toBe('passed');
  expect(tested.capabilities.tools).toBe(true);
  expect(tested.capabilities.stream).toBe(true);
  owned.question = 'FIXTURE_GET ' + JSON.stringify({
    space_id: owned.space.id, page_id: owned.page.page_id, revision_id: owned.revision.id,
  });
}

export async function gate(operation, revisionID) {
  const response = await fetch('http://agent-model-provider:8080/fixtures/' + operation, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision_id: revisionID }),
  });
  expect(response.status, 'Controlled model streaming gate status').toBe(200);
}

export async function gateState(revisionID) {
  const response = await fetch('http://agent-model-provider:8080/fixtures/gates/' + revisionID);
  expect(response.status).toBe(200);
  return response.json();
}

export async function cleanup(page, owned) {
  const results = [];
  async function attempt(label, fn) {
    try { await fn(); results.push({ action: label, ok: true }); }
    catch { results.push({ action: label, ok: false }); }
  }
  if (owned.revision) await attempt('release-own-model-gate', () => gate('releases', owned.revision.id));
  if (owned.run) await attempt('clear-own-session', () => api(page, 'DELETE', `/sessions/${owned.run.session_id}`));
  if (owned.agent) await attempt('unpublish-own-agent', () => api(page, 'POST', `/agents/${owned.agent.id}/publish`, {
    base_configuration_id: owned.agent.configuration_id, shared: false,
  }));
  if (owned.model) await attempt('retire-own-model', () => api(page, 'DELETE',
    `/models/${owned.model.id}?base_configuration_id=${encodeURIComponent(owned.model.configuration_id)}`));
  if (owned.space) for (const action of ['read', 'write'])
    await attempt('revoke-own-space-' + action, () => api(page, 'PUT', '/grants', {
      space_id: owned.space.id, action, subjects: [],
    }));
  record('assets-' + owned.suffix, {
    fixture: 'deterministic_protocol_simulation', space_id: owned.space?.id,
    source_id: owned.source?.id, page_id: owned.page?.page_id,
    revision_id: owned.revision?.id, model_id: owned.model?.id,
    agent_id: owned.agent?.id, run_id: owned.run?.id, cleanup: results,
    retained: 'Tagged immutable source/revision/audit records are retained; no unrelated assets changed.',
  });
  expect(results.every(result => result.ok), 'Exact test asset cleanup; inspect redacted asset report').toBe(true);
}
