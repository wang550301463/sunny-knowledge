import { test, expect, publicURL, realLogin, api, request, drawer, assets, seed, cleanup, record, artifacts } from './fixtures.js';
import { exerciseMissingEdge, releaseFault } from './fault-gate.js';

async function setCheckbox(locator, checked) {
  // Ant Design's controlled group commits after the native click. Click once,
  // then observe React's result; never toggle repeatedly to satisfy an assertion.
  if (await locator.isChecked() !== checked) await locator.click();
  if (checked) await expect(locator).toBeChecked();
  else await expect(locator).not.toBeChecked();
}

function responseFor(page, path, predicate) {
  return page.waitForResponse(response => {
    if (new URL(response.url()).pathname !== '/api/v1' + path || response.request().method() !== 'POST') return false;
    try { return predicate(response.request().postDataJSON()); } catch { return false; }
  });
}
async function graphResponse(promise) {
  const response = await promise;
  expect(response.status(), 'Real authorized Retrieval response').toBe(200);
  return response.json();
}
async function sourceDrawer(page, owned, edge) {
  await page.getByRole('button', { name: '查看关系 ' + edge.id, exact: true }).click();
  const panel = drawer(page, '图谱证据');
  await expect(panel).toBeVisible();
  const sourceRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/source-snapshots/' + owned.reference.revision_id);
  await panel.getByRole('button', { name: '查看原文 java/pom.xml', exact: true }).click();
  const response = await sourceRead;
  expect(response.status()).toBe(200);
  const snapshot = await response.json();
  expect(snapshot.id).toBe(owned.snapshot.id);
  expect(snapshot.source_revision).toBe(owned.manifest.commits.v1);
  expect(snapshot.sha256).toBe(owned.snapshot.sha256);
  await expect(panel.getByText('已复核图谱映射及原始来源授权；展示固定来源修订。')).toBeVisible();
  await expect(panel.locator('.source-code')).toContainText('<artifactId>guava</artifactId>');
  return panel;
}

test('real PKCE and Git-derived Neo4j paths, exact source, direction/time, missing-edge degradation and live ACL withdrawal', async ({ page }) => {
  const owned = assets();
  const evidence = { fixture: 'real_git_and_neo4j_with_protocol_model_simulation', checks: [] };
  const calls = [];
  const observe = req => {
    const path = new URL(req.url()).pathname;
    if (path === '/api/v1/search' || path === '/api/v1/traverse') calls.push({ path, at: Date.now() });
  };
  page.on('request', observe);
  try {
    evidence.login = await realLogin(page);
    await seed(page, owned);
    calls.length = 0;
    await page.goto(`${publicURL}/spaces/${owned.space.id}?tab=graph`);
    await expect(page.getByRole('heading', { name: owned.name, exact: true })).toBeVisible();
    const explorer = page.getByRole('region', { name: '知识图谱浏览器', exact: true });

    await test.step('Search and choose a real dependency seed, then traverse one incoming hop', async () => {
      await explorer.getByRole('textbox', { name: '查找起点', exact: true }).fill('com.google.guava:guava');
      await setCheckbox(explorer.getByRole('checkbox', { name: 'uses', exact: true }), false);
      await explorer.getByLabel('遍历方向', { exact: true }).selectOption('incoming');
      const searched = responseFor(page, '/search', body => body.query === 'com.google.guava:guava');
      await explorer.getByRole('button', { name: '查找证据片段', exact: true }).click();
      const results = await graphResponse(searched);
      expect(results.items.every(item => item.space_id === owned.space.id)).toBe(true);
      const index = results.items.findIndex(item => item.id === owned.seed);
      expect(index, 'Actual BM25/vector search must recall the known declared dependency fragment').toBeGreaterThanOrEqual(0);
      await setCheckbox(explorer.locator('.graph-seeds .ant-card').nth(index).getByRole('checkbox'), true);
      const expanded = responseFor(page, '/traverse', body => body.relation?.direction === 'incoming' && body.relation?.hops === 1 && body.seed_fragment_ids?.includes(owned.seed));
      await explorer.getByRole('button', { name: '展开关系', exact: true }).click();
      owned.oneHop = await graphResponse(expanded);
      expect(owned.oneHop.graph.edges.map(edge => [edge.source, edge.target, edge.type])).toEqual([owned.expectedEdges[1]]);
      expect(owned.oneHop.graph.paths.every(path => path.edge_ids.length <= 1)).toBe(true);
      await expect(page.getByRole('img', { name: '授权关系图' })).toBeVisible();
      await expect(explorer.locator('[aria-label="当前执行条件"]')).toContainText('入向 · 1 跳');
      await page.screenshot({ path: `${artifacts}/one-hop.png`, fullPage: true, animations: 'disabled' });
      evidence.checks.push('Browser search recalled an actual canonical dependency fragment; one-hop incoming traversal preserved actual edge direction');
    });

    await test.step('Change only draft controls, then display the actual two-hop sibling dependency path', async () => {
      await explorer.getByLabel('遍历方向', { exact: true }).selectOption('both');
      await explorer.getByLabel('遍历跳数', { exact: true }).selectOption('2');
      await expect(explorer.locator('[aria-label="当前执行条件"]')).toContainText('入向 · 1 跳');
      await expect(explorer.getByText('条件已修改，重新查询后应用。', { exact: true })).toBeVisible();
      const expanded = responseFor(page, '/traverse', body => body.relation?.direction === 'both' && body.relation?.hops === 2);
      await explorer.getByRole('button', { name: '按新条件展开', exact: true }).click();
      owned.twoHop = await graphResponse(expanded);
      expect(owned.twoHop.graph.edges.map(edge => [edge.source, edge.target, edge.type])).toEqual(expect.arrayContaining(owned.expectedEdges));
      expect(owned.twoHop.graph.paths.some(path => path.edge_ids.length === 2 && path.node_ids[0] === owned.dependencies[1].id && path.node_ids[2] === owned.dependencies[0].id)).toBe(true);
      expect(owned.twoHop.degraded).toEqual([]);
      expect(owned.twoHop.graph.degraded).toEqual([]);
      await expect(explorer.locator('[aria-label="当前执行条件"]')).toContainText('双向 · 2 跳');
      await explorer.getByText(/返回的证据路径（/).click();
      await expect(explorer.locator('.graph-paths')).toContainText(owned.dependencies[0].id);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `${artifacts}/two-hop.png`, fullPage: true, animations: 'disabled' });
      evidence.checks.push('Actual two-hop path dependency → module → sibling dependency, separate draft and executed controls, no horizontal page overflow');
    });

    await test.step('Read the commit-pinned exact source after fresh live graph authorization', async () => {
      const edge = owned.twoHop.graph.edges.find(item => item.target === owned.dependencies[1].id);
      const panel = await sourceDrawer(page, owned, edge);
      // Disable animations for the final raster as well as awaiting visible content;
      // never capture login fields, token storage or HTTP headers.
      await panel.locator('.source-code').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${artifacts}/exact-source.png`, fullPage: true, animations: 'disabled' });
      await panel.getByRole('button', { name: '关闭', exact: true }).click();
      await expect(panel).not.toBeVisible();
      evidence.checks.push('Exact source hash, commit, snapshot and visible original XML; raw source preceded and followed by real traversal checks');
    });

    await test.step('Apply real business and knowledge cutoffs with explicit historical capability notice', async () => {
      const cutoff = new Date(Date.now() + 120_000).toISOString().slice(0, 16);
      const expected = cutoff + ':00.000Z';
      await explorer.getByLabel('业务有效时间', { exact: true }).fill(cutoff);
      await explorer.getByLabel('系统获知截止时间', { exact: true }).fill(cutoff);
      await setCheckbox(explorer.getByRole('checkbox', { name: '包含已投影历史修订', exact: true }), true);
      const expanded = responseFor(page, '/traverse', body => body.as_of === expected && body.known_at === expected && body.include_historical === true);
      await explorer.getByRole('button', { name: '按新条件展开', exact: true }).click();
      const historical = await graphResponse(expanded);
      expect(Date.parse(historical.as_of)).toBe(Date.parse(expected));
      expect(Date.parse(historical.known_at)).toBe(Date.parse(expected));
      expect(historical.gaps).toContain('history_contains_projected_revisions_only');
      expect(historical.graph.edges.length).toBeGreaterThanOrEqual(2);
      await expect(explorer.getByText('历史查询仅包含已完成投影的修订，并非完整历史档案。', { exact: true })).toBeVisible();
      await expect(explorer.getByText(/没有部署证据，不能据此推断生产版本/)).toBeVisible();
      await page.screenshot({ path: `${artifacts}/time-and-history.png`, fullPage: true, animations: 'disabled' });
      // Restore current semantics before the physical fault and live revocation checks.
      await explorer.getByLabel('业务有效时间', { exact: true }).fill('');
      await explorer.getByLabel('系统获知截止时间', { exact: true }).fill('');
      await setCheckbox(explorer.getByRole('checkbox', { name: '包含已投影历史修订', exact: true }), false);
      const current = responseFor(page, '/traverse', body => body.as_of === null && body.known_at === null && body.include_historical === false);
      await explorer.getByRole('button', { name: '按新条件展开', exact: true }).click();
      await graphResponse(current);
      evidence.checks.push('Actual as_of and known_at request/response, explicit partial-history and no-production-inference notice');
    });

    await test.step('Show a real missing projection as degraded capability, then recover', async () => {
      await exerciseMissingEdge(page, owned, explorer);
      evidence.checks.push('One exact test projection edge physically removed from Neo4j; browser displayed graph_projection_pending and recovered after exact restoration');
    });

    await test.step('Withdraw graph and raw source after actual same-source ACL tightening, then withdraw the space header', async () => {
      const edge = owned.twoHop.graph.edges.find(item => item.target === owned.dependencies[1].id);
      await sourceDrawer(page, owned, edge);
      await api(page, 'PUT', '/grants', {
        space_id: owned.space.id, resource_id: owned.source.resource_id,
        action: 'read', subjects: owned.workers,
      });
      await expect(page.getByRole('img', { name: '授权关系图' })).toHaveCount(0);
      await expect(drawer(page, '图谱证据')).not.toBeVisible();
      await expect(page.locator('.source-code')).toHaveCount(0);
      await expect(explorer.getByRole('button', { name: /^查看关系 / })).toHaveCount(0);
      await expect(page.getByRole('heading', { name: owned.name, exact: true })).toBeVisible();
      expect((await request(page, 'GET', '/source-snapshots/' + owned.reference.revision_id)).status).toBe(403);
      const denied = await api(page, 'POST', '/traverse', {
        space_ids: [owned.space.id], seed_fragment_ids: [owned.seed],
        relation: { types: ['depends_on'], direction: 'both', hops: 2 },
      });
      expect(denied.items).toEqual([]);
      expect(denied.evidence).toEqual([]);
      expect(denied.graph.edges).toEqual([]);
      await page.screenshot({ path: `${artifacts}/source-revoked.png`, fullPage: true, animations: 'disabled' });
      await api(page, 'PUT', '/grants', { space_id: owned.space.id, action: 'read', subjects: owned.workers });
      await expect(page.getByRole('heading', { name: owned.name, exact: true })).toHaveCount(0);
      await expect(explorer.getByRole('button', { name: '查找证据片段', exact: true })).toBeDisabled();
      await expect(explorer.getByRole('button', { name: /^查看关系 / })).toHaveCount(0);
      expect((await request(page, 'GET', '/spaces/' + owned.space.id)).status).toBe(403);
      await page.screenshot({ path: `${artifacts}/space-revoked.png`, fullPage: true, animations: 'disabled' });
      evidence.checks.push('Current source ACL revoked real graph/source access while space remained readable; subsequent space read revocation withdrew header and disabled graph reads');
    });
    expect(calls.filter(call => call.path === '/api/v1/search')).toHaveLength(1);
    evidence.browser_requests = { search: 1, traverse: calls.filter(call => call.path === '/api/v1/traverse').length };
  } finally {
    page.off('request', observe);
    await releaseFault(owned);
    record('acceptance-' + owned.suffix, evidence);
    await cleanup(page, owned);
  }
});
