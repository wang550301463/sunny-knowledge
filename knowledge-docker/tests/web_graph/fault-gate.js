import { mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { expect, artifacts } from './fixtures.js';

const root = '/run/web-graph-fixture';
function write(name, value) {
  mkdirSync(root, { recursive: true });
  writeFileSync(`${root}/${name}.tmp`, JSON.stringify(value) + '\n');
  renameSync(`${root}/${name}.tmp`, `${root}/${name}`);
}
function state(owned) {
  try {
    const value = JSON.parse(readFileSync(`${root}/state.json`, 'utf8'));
    return value.fixture_id === owned.suffix ? value.state : 'waiting';
  } catch { return 'waiting'; }
}
export async function releaseFault(owned) {
  if (!owned.faultRequested) return;
  write('release.json', { fixture_id: owned.suffix });
}
export async function exerciseMissingEdge(page, owned, explorer) {
  expect(process.env.WEB_GRAPH_FAULT_GATE, 'The real missing-edge controller is mandatory; this test never silently skips fault acceptance').toBe('true');
  const edge = owned.twoHop.graph.edges.find(item => item.target === owned.dependencies[0].id);
  owned.faultRequested = true;
  write('request.json', {
    fixture_id: owned.suffix, created_at: Math.floor(Date.now() / 1000),
    namespace: owned.graphNamespace, space_id: owned.space.id,
    page_id: owned.pom.id, revision_id: owned.pom.revision.id, edge_id: edge.id,
  });
  try {
    await expect.poll(() => state(owned), { timeout: 180_000, intervals: [250, 500] }).toBe('fault_applied');
    await expect(explorer.getByRole('button', { name: '复核当前结果', exact: true })).toBeEnabled();
    await explorer.getByRole('button', { name: '复核当前结果', exact: true }).click();
    await expect(explorer.getByText('图投影尚未就绪，部分关系或证据映射正在等待重建。', { exact: true })).toBeVisible();
    await expect(explorer.getByText(/当前图不完整，不能据此判断不存在依赖/)).toBeVisible();
    await expect(explorer.getByRole('button', { name: '查看关系 ' + edge.id, exact: true })).toHaveCount(0);
    await page.screenshot({ path: `${artifacts}/physical-edge-degraded.png`, fullPage: true, animations: 'disabled' });
  } finally { await releaseFault(owned); }
  await expect.poll(() => state(owned), { timeout: 30_000, intervals: [250, 500] }).toBe('restored');
  await expect(explorer.getByRole('button', { name: '复核当前结果', exact: true })).toBeEnabled();
  await explorer.getByRole('button', { name: '复核当前结果', exact: true }).click();
  await expect(explorer.getByRole('button', { name: '查看关系 ' + edge.id, exact: true })).toBeVisible();
  await expect(explorer.getByText('图投影尚未就绪，部分关系或证据映射正在等待重建。', { exact: true })).toHaveCount(0);
}
