import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { test, expect, publicURL, realLogin, api, request, drawer, selectOption } from '../web_agent/fixtures.js';
import { workerID } from '../web/fixtures.js';

export { test, expect, publicURL, realLogin, api, request, drawer, selectOption };
export const output = '/artifacts/web-onboarding';
export const provider = 'http://onboarding-model-provider:8080';

export function record(name, value) {
  mkdirSync(output, { recursive: true });
  writeFileSync(`${output}/${name}.json`, JSON.stringify(value, null, 2) + '\n');
}

export function assets() {
  const suffix = randomUUID().replaceAll('-', '');
  return {
    suffix, name: '首用浏览器验收-' + suffix.slice(0, 8),
    marker: 'onboarding-evidence-' + suffix,
    path: 'docs/onboarding-' + suffix + '.md',
  };
}

export async function setup(page, owned) {
  if (typeof workerID !== 'string' || !workerID)
    throw new Error('An explicitly enrolled ingest worker is required; no fallback principal is assigned');
  const me = await api(page, 'GET', '/me');
  expect(me.permissions).toContain('platform_admin');
  owned.user = 'user:' + me.id;
  owned.worker = 'service:' + workerID;
  owned.space = await api(page, 'POST', '/spaces', { name: owned.name }, 201);
  for (const action of ['read', 'write'])
    await api(page, 'PUT', '/grants', { space_id: owned.space.id, action, subjects: [owned.user, owned.worker] });
  owned.model = await api(page, 'POST', '/models', {
    name: owned.name + ' Chat 协议模拟', provider: 'openai',
    provider_model: 'protocol-fixture-chat', capability: 'chat',
    base_url: provider + '/v1', max_input_chars: 100000,
    max_output_tokens: 2048, timeout_seconds: 30, max_retries: 0,
  }, 201);
  const checked = await api(page, 'POST', `/models/${owned.model.id}/test`, {
    configuration_id: owned.model.configuration_id, test_tools: true, test_stream: true,
  });
  expect(checked.test_state).toBe('passed');
  expect(checked.capabilities).toMatchObject({ chat: true, tools: true, stream: true });
}

export async function prepareAgent(page, owned) {
  owned.agent = await api(page, 'POST', '/agents', {
    name: owned.name, description: 'Isolated onboarding browser protocol acceptance',
    owner_space_id: owned.space.id,
    config: { mode: 'knowledge_qa', model_configuration_id: owned.model.configuration_id,
      space_ids: [owned.space.id], tools: ['get'], max_output_tokens: 2048 },
  }, 201);
  await api(page, 'POST', `/agents/${owned.agent.id}/publish`, {
    base_configuration_id: owned.agent.configuration_id, shared: true,
  });
  owned.session = await api(page, 'POST', '/sessions', { title: owned.name + ' 会话' }, 201);
  owned.question = 'FIXTURE_GET ' + JSON.stringify({
    space_id: owned.space.id, page_id: owned.item.page_id, revision_id: owned.revision.id,
  });
}

export async function gate(operation, revision) {
  const response = await fetch(provider + '/fixtures/' + operation, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision_id: revision }), signal: AbortSignal.timeout(5000),
  });
  expect(response.status, 'Own protocol provider gate').toBe(200);
}

export async function cleanup(page, owned) {
  const results = [];
  async function attempt(action, work) {
    try { await work(); results.push({ action, confirmed: true }); }
    catch { results.push({ action, confirmed: false }); }
  }
  if (owned.revision) await attempt('release-own-model-gate', () => gate('releases', owned.revision.id));
  if (owned.session) await attempt('clear-own-session', () => api(page, 'DELETE', `/sessions/${owned.session.id}`));
  if (owned.agent) await attempt('unpublish-own-agent', () => api(page, 'POST', `/agents/${owned.agent.id}/publish`, {
    base_configuration_id: owned.agent.configuration_id, shared: false,
  }));
  if (owned.model) await attempt('retire-own-model', () => api(page, 'DELETE',
    `/models/${owned.model.id}?base_configuration_id=${encodeURIComponent(owned.model.configuration_id)}`));
  if (owned.source) await attempt('revoke-own-source-read', () => api(page, 'PUT', '/grants', {
    space_id: owned.space.id, resource_id: owned.source.resource_id, action: 'read', subjects: [],
  }));
  if (owned.space) for (const action of ['read', 'write'])
    await attempt('revoke-own-space-' + action, () => api(page, 'PUT', '/grants', {
      space_id: owned.space.id, action, subjects: [],
    }));
  record('assets-' + owned.suffix, {
    fixture: 'deterministic_chat_protocol_simulation', space_id: owned.space?.id,
    source_id: owned.source?.id, task_id: owned.task?.id, page_id: owned.item?.page_id,
    revision_id: owned.revision?.id, model_id: owned.model?.id,
    agent_id: owned.agent?.id, session_id: owned.session?.id, run_id: owned.run?.id,
    cleanup: results,
    retained: 'Only own tagged immutable source/revision/audit records remain; no unrelated assets, runtime configuration or grants changed.',
  });
  expect(results.every(item => item.confirmed), 'Exact fixture cleanup must be confirmed; see redacted asset report').toBe(true);
}
