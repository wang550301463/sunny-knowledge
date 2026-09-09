import { createHash, randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { test, expect, publicURL, realLogin, api, request, drawer } from '../web_agent/fixtures.js';

export { test, expect, publicURL, realLogin, api, request, drawer };
export const artifacts = '/artifacts/web-graph';
const configuration = JSON.parse(readFileSync('/run/knowledge/test-env.json', 'utf8'));
const fixtureURL = 'http://web-graph-git-fixture:8080';
export function record(name, value) {
  mkdirSync(artifacts, { recursive: true });
  writeFileSync(`${artifacts}/${name}.json`, JSON.stringify(value, null, 2) + '\n');
}
export function assets() {
  const suffix = randomUUID().replaceAll('-', '');
  return { suffix, name: '真实图谱浏览器验收-' + suffix.slice(0, 10), cleanup: [] };
}
export function fragment(page, claimID) {
  return createHash('sha256').update(JSON.stringify([page.id, page.revision.id, 'claim', claimID, 0])).digest('hex');
}
export async function seed(page, owned) {
  const settings = JSON.parse(readFileSync('/run/knowledge/git-graph-regression.json', 'utf8'));
  expect(settings.state, 'Projection runtime must be explicitly provisioned').toBe('ready');
  expect(settings.model_kind).toBe('deterministic_protocol_simulation');
  expect(settings.graph_namespace).toMatch(/^git-graph-regression-[0-9a-f]{32}$/);
  owned.graphNamespace = settings.graph_namespace;
  const manifestResponse = await fetch(fixtureURL + '/manifest.json');
  expect(manifestResponse.status).toBe(200);
  owned.manifest = await manifestResponse.json();
  expect(owned.manifest.kind).toBe('web_graph_three_language_git_fixture');
  const me = await api(page, 'GET', '/me');
  owned.actor = 'user:' + me.id;
  owned.workers = ['ingest', 'retrieval', 'graphiti'].map(service => {
    const id = configuration[service + '_principal_id'];
    expect(typeof id, `Preconfigured ${service} worker identity`).toBe('string');
    return 'service:' + id;
  });
  owned.space = await api(page, 'POST', '/spaces', { name: owned.name }, 201);
  await api(page, 'PUT', '/grants', { space_id: owned.space.id, action: 'read', subjects: [owned.actor, ...owned.workers] });
  await api(page, 'PUT', '/grants', { space_id: owned.space.id, action: 'write', subjects: [owned.actor, owned.workers[0]] });
  owned.source = await api(page, 'POST', '/sources', {
    name: owned.name, kind: 'git', space_id: owned.space.id,
    config: { url: fixtureURL + owned.manifest.repository_path, ref: owned.manifest.refs.v1 },
  }, 201);
  const preview = await api(page, 'POST', `/sources/${owned.source.id}/preview`, { base_version: 1 });
  expect(preview.source_revision).toBe(owned.manifest.commits.v1);
  expect(preview.file_count).toBe(Object.keys(owned.manifest.files.v1).length);
  let task = await api(page, 'POST', `/sources/${owned.source.id}/sync`, { base_version: 1 }, 202);
  await expect.poll(async () => {
    task = await api(page, 'GET', `/tasks/${task.id}`);
    return task.status;
  }, { timeout: 180_000, intervals: [500, 1000] }).toBe('review_needed');
  owned.pages = {};
  for (const item of task.result.items) {
    if (item.path === 'README.md') {
      expect(item.status).toBe('review_needed');
      await api(page, 'POST', `/reviews/${item.proposal_id}/approve`, {
        reason: 'Review the exact isolated Git fixture narrative for browser-to-Neo4j acceptance',
      });
    } else expect(item.status, 'Static fixture facts must publish through the deterministic compiler').toBe('published');
    owned.pages[item.path] = await api(page, 'GET', '/pages/' + encodeURIComponent(item.page_id));
  }
  for (const path of ['java/src/main/java/example/Payment.java', 'ts/src/payment.ts', 'go/payment.go'])
    expect(owned.pages[path]?.revision.content.claims.some(claim => claim.text.startsWith('Declares '))).toBe(true);
  owned.pom = owned.pages['java/pom.xml'];
  const claims = owned.pom.revision.content.claims;
  owned.module = claims.find(claim => claim.entity_type === 'Module');
  owned.dependencies = owned.manifest.dependencies.map(name => claims.find(claim => claim.entity?.name === name));
  expect(owned.module).toBeTruthy();
  expect(owned.dependencies.every(Boolean)).toBe(true);
  for (const target of owned.dependencies)
    expect(claims.some(claim => claim.relation?.source_id === owned.module.id && claim.relation?.target_id === target.id && claim.relation?.type === 'depends_on')).toBe(true);
  owned.seed = fragment(owned.pom, owned.dependencies[1].id);
  owned.expectedEdges = owned.dependencies.map(target => [owned.module.id, target.id, 'depends_on']);
  // A bounded projection wait covers the existing durable outbox lease after a
  // previous failed delivery; it does not assert interactive latency.
  await expect.poll(async () => {
    const result = await request(page, 'POST', '/traverse', {
      space_ids: [owned.space.id], seed_fragment_ids: [owned.seed],
      relation: { types: ['depends_on'], direction: 'both', hops: 2 },
    });
    expect([200, 503]).toContain(result.status);
    if (result.status !== 200) return false;
    const data = result.data;
    const edges = new Set(data.graph.edges.map(edge => JSON.stringify([edge.source, edge.target, edge.type])));
    const present = owned.expectedEdges.every(edge => edges.has(JSON.stringify(edge)))
      && data.graph.paths.some(path => path.edge_ids.length === 2)
      && !data.degraded.length && !data.graph.degraded.length;
    if (present) owned.readyGraph = data;
    return present;
  }, { timeout: 420_000, intervals: [1000, 2000] }).toBe(true);
  owned.reference = owned.dependencies[1].evidence[0];
  owned.snapshot = await api(page, 'GET', '/source-snapshots/' + encodeURIComponent(owned.reference.revision_id));
  expect(owned.snapshot.sha256).toBe(owned.manifest.files.v1['java/pom.xml'].sha256);
  expect(createHash('sha256').update(owned.snapshot.text).digest('hex')).toBe(owned.snapshot.sha256);
  expect(owned.snapshot.source_revision).toBe(owned.manifest.commits.v1);
  owned.started = Math.floor(Date.now() / 1000);
  mkdirSync('/run/web-graph-fixture', { recursive: true });
  writeFileSync('/run/web-graph-fixture/journal.json.tmp', JSON.stringify({
    fixture_id: owned.suffix, namespace: owned.graphNamespace, space_id: owned.space.id,
    source_id: owned.source.id, page_id: owned.pom.id, revision_id: owned.pom.revision.id,
    edge_ids: owned.readyGraph.graph.edges.map(edge => edge.id), created_at: owned.started,
  }) + '\n');
  renameSync('/run/web-graph-fixture/journal.json.tmp', '/run/web-graph-fixture/journal.json');
}
export async function cleanup(page, owned) {
  async function attempt(action, body) {
    try { await api(page, 'PUT', '/grants', body); owned.cleanup.push({ action, ok: true }); }
    catch { owned.cleanup.push({ action, ok: false }); }
  }
  if (owned.space) for (const action of ['read', 'write'])
    await attempt('revoke-own-space-' + action, { space_id: owned.space.id, action, subjects: [] });
  record('assets-' + owned.suffix, {
    fixture: 'real_git_and_neo4j_with_protocol_model_simulation', space_id: owned.space?.id,
    source_id: owned.source?.id, page_id: owned.pom?.id, source_revision: owned.manifest?.commits.v1,
    namespace: owned.graphNamespace, cleanup: owned.cleanup,
    retained: 'Exact tagged immutable snapshots, revisions and audit records retained with no space access; model/runtime assets are managed separately by the operator.',
  });
  expect(owned.cleanup.every(result => result.ok), 'Exact fixture asset cleanup; inspect redacted report').toBe(true);
}
