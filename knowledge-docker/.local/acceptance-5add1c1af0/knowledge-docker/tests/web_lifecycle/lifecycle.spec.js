import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { test, expect, publicURL, realLogin, api, request, drawer } from '../web_agent/fixtures.js';
import { workerID } from '../web/fixtures.js';

const output = '/artifacts/web-lifecycle';
function record(name, value) {
  mkdirSync(output, { recursive: true });
  writeFileSync(`${output}/${name}.json`, JSON.stringify(value, null, 2) + '\n');
}
function usage(panel, label) {
  return panel.locator('.ant-descriptions-row').filter({ hasText: label }).locator('.ant-descriptions-item-content');
}

test('real Wiki display records own usage once, preserves source age and withdraws current/pinned/source data after actual revocation', async ({ page }) => {
  const owned = { suffix: randomUUID().replaceAll('-', ''), cleanup: [] };
  const proof = { runtime: 'real_browser_pkce_ingest_canonical_pg_s3_auth', checks: [] };
  const events = [];
  const observe = req => {
    if (owned.page && decodeURIComponent(new URL(req.url()).pathname) === `/api/v1/pages/${owned.page.id}/access-events`)
      events.push(req.postDataJSON());
  };
  page.on('request', observe);
  try {
    proof.login = await realLogin(page);
    const me = await api(page, 'GET', '/me');
    owned.actor = 'user:' + me.id;
    owned.space = await api(page, 'POST', '/spaces', { name: '生命周期浏览器验收-' + owned.suffix.slice(0, 8) }, 201);
    for (const action of ['read', 'write'])
      await api(page, 'PUT', '/grants', { space_id: owned.space.id, action, subjects: [owned.actor, 'service:' + workerID] });
    const marker = 'lifecycle-source-' + owned.suffix;
    const sourceResponse = await request(page, 'POST', '/sources', {
      name: marker, kind: 'markdown', space_id: owned.space.id,
      config: { path: 'docs/lifecycle-browser.md', content: `# ${marker}\r\n访问只记录使用情况，不提高事实可信度。\r\n` },
    });
    proof.source_create = { status: sourceResponse.status, code: sourceResponse.data?.error?.code };
    expect(sourceResponse.status, 'Actual source creation: ' + (proof.source_create.code ?? 'created')).toBe(201);
    owned.source = sourceResponse.data;
    const preview = await api(page, 'POST', `/sources/${owned.source.id}/preview`, { base_version: 1 });
    expect(preview.file_count).toBe(1);
    let task = await api(page, 'POST', `/sources/${owned.source.id}/sync`, { base_version: 1 }, 202);
    await expect.poll(async () => {
      task = await api(page, 'GET', `/tasks/${task.id}`);
      return task.status;
    }, { timeout: 150_000, intervals: [500, 1000, 2000] }).toBe('review_needed');
    expect(task.result.items).toHaveLength(1);
    const item = task.result.items[0];
    await api(page, 'POST', `/reviews/${item.proposal_id}/approve`, { reason: 'Verify exact isolated Markdown evidence before lifecycle acceptance' });
    owned.page = await api(page, 'GET', `/pages/${item.page_id}`);
    const revision = owned.page.revision;
    const reference = revision.content.evidence[0];
    const metricsPath = `/pages/${owned.page.id}/lifecycle?revision_id=${revision.id}`;
    const baseline = await api(page, 'GET', metricsPath);
    expect(baseline.support.registered_source_count).toBe(1);
    expect(baseline.freshness.observed_at).toBeTruthy();
    expect(baseline.access.mine_visits_7d).toBe(0);
    expect(events).toHaveLength(0);
    proof.checks.push('Real source preview, Temporal ingest, review-approved publication; direct GET does not record access');

    const currentPath = `/pages/${owned.page.id}`;
    await page.goto(publicURL + currentPath);
    let panel = page.getByRole('region', { name: '知识生命周期', exact: true });
    await expect(panel).toBeVisible();
    await expect(usage(panel, '我的近 7 天访问次数')).toHaveText('1');
    expect(events).toHaveLength(1);
    const first = events[0];
    expect(first.revision_id).toBe(revision.id);
    expect(first.idempotency_key).toBeTruthy();
    await panel.getByRole('button', { name: '刷新指标', exact: true }).click();
    await expect(usage(panel, '我的近 7 天访问次数')).toHaveText('1');
    const firstMetrics = await api(page, 'GET', metricsPath);
    expect(firstMetrics.freshness.observed_at).toBe(baseline.freshness.observed_at);
    expect(firstMetrics.freshness.score).toBeLessThanOrEqual(baseline.freshness.score);
    const receipt = await api(page, 'POST', `/pages/${owned.page.id}/access-events`, first, 201);
    const replay = await api(page, 'POST', `/pages/${owned.page.id}/access-events`, first, 201);
    expect(replay).toEqual(receipt);
    expect((await api(page, 'GET', metricsPath)).access.mine_visits_7d).toBe(1);
    const beforeReload = events.length;
    await page.reload();
    await expect(usage(panel, '我的近 7 天访问次数')).toHaveText('1');
    expect(events).toHaveLength(beforeReload);
    proof.checks.push('Visible current revision records once; metric refresh, full reload and exact POST replay do not add visits or reset source age');
    await page.screenshot({ path: `${output}/current-usage.png`, fullPage: true, animations: 'disabled' });

    // A separate route-history entry for the exact pinned revision is a new display.
    await page.goto(publicURL + currentPath + '?revision=' + revision.id);
    await expect(page.getByText('正在查看链接指定的不可变版本', { exact: true })).toBeVisible();
    await expect(usage(panel, '我的近 7 天访问次数')).toHaveText('2');
    expect(events.at(-1).revision_id).toBe(revision.id);
    expect(events.at(-1).idempotency_key).not.toBe(first.idempotency_key);
    await page.getByRole('button', { name: /docs\/lifecycle-browser\.md/ }).first().click();
    const citation = drawer(page, '原文引用');
    await expect(citation).toBeVisible();
    await expect(citation.locator('.source-code')).toContainText('访问只记录使用情况，不提高事实可信度。');
    const snapshot = await api(page, 'GET', '/source-snapshots/' + reference.revision_id);
    expect(snapshot.source_revision).toBe(preview.source_revision);
    await page.screenshot({ path: `${output}/pinned-source.png`, fullPage: true, animations: 'disabled' });
    proof.checks.push('Pinned navigation records its own display and opens exact immutable original source through current authorization');

    await api(page, 'PUT', '/grants', { space_id: owned.space.id, resource_id: reference.resource_id, action: 'read', subjects: [] });
    await expect(panel).not.toBeVisible();
    await expect(page.getByRole('heading', { name: marker, exact: true })).toHaveCount(0);
    await expect(citation).not.toBeVisible();
    await expect(page.locator('.source-code')).toHaveCount(0);
    for (const path of [metricsPath, currentPath, `${currentPath}/revisions/${revision.id}`, '/source-snapshots/' + reference.revision_id])
      expect((await request(page, 'GET', path)).status, 'Current/pinned/lifecycle/original read after source revocation').toBe(403);
    expect((await request(page, 'POST', `${currentPath}/access-events`, first)).status).toBe(403);
    await page.reload();
    await expect(panel).not.toBeVisible();
    await expect(page.getByRole('alert')).toContainText('权限不足');
    await page.screenshot({ path: `${output}/revoked.png`, fullPage: true, animations: 'disabled' });
    proof.checks.push('Actual source ACL revocation withdraws open pinned body/metrics/drawer; current/history/source/metrics/receipt replay deny and reload does not restore content');
    proof.state = 'passed';
  } finally {
    page.off('request', observe);
    if (owned.space) {
      for (const action of ['read', 'write']) {
        try {
          await api(page, 'PUT', '/grants', { space_id: owned.space.id, action, subjects: [] });
          owned.cleanup.push(action + '_revoked');
        } catch { owned.cleanup.push(action + '_unconfirmed'); }
      }
    }
    record('assertions', proof);
    record('cleanup', { space_id: owned.space?.id, source_id: owned.source?.id, page_id: owned.page?.id, actions: owned.cleanup });
    expect(owned.cleanup.filter(state => state.endsWith('_unconfirmed'))).toEqual([]);
  }
});
