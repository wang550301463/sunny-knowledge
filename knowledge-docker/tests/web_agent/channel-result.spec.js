import { readFileSync, writeFileSync } from 'node:fs';
import { test, expect, publicURL, realLogin, api, record, drawer as findDrawer } from './fixtures.js';

test('real channel result remains run-bound and readonly, including citation and revocation', async ({ page }) => {
  // Root's actual HTTP/WS integration owns this short-lived run and its cleanup.
  // The handoff contains only fixture IDs/text; it never contains binding proof or bearer.
  await realLogin(page);
  let fixture;
  await expect.poll(() => {
    try {
      const value = JSON.parse(readFileSync('/run/channel-fixture/fixture.json', 'utf8'));
      if (value.available === false || typeof value.created_at !== 'number'
        || Date.now() - value.created_at * 1000 > 115_000
        || value.created_at * 1000 > Date.now() + 10_000) return false;
      if (['fixture_id', 'run_id', 'marker', 'channel_id', 'source_resource_id', 'space_id']
        .some(key => typeof value[key] !== 'string' || !value[key])) return false;
      fixture = value;
      return true;
    } catch { return false; }
  }, { timeout: 60_000, intervals: [250, 500, 1000] }).toBe(true);
  let released = false;
  const release = () => {
    writeFileSync('/run/channel-fixture/release', fixture.fixture_id);
    released = true;
  };
  const calls = [];
  const observe = request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/v1/')) calls.push({ method: request.method(), path });
  };
  page.on('request', observe);
  try {
    await page.goto(`${publicURL}/runs/${fixture.run_id}`);
    await expect(page.getByText('企微问答结果 · 只读', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: '事实' })).toContainText(fixture.marker);
    for (const label of ['停止', '重新运行', '新建会话', '清空会话'])
      await expect(page.getByRole('button', { name: label, exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /反\s*馈/, exact: true })).toHaveCount(0);
    await expect(page.getByRole('textbox', { name: '问题' })).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const run = await api(page, 'GET', `/runs/${fixture.run_id}`);
    expect(run.entrypoint).toBe('channel');
    const citation = run.citations[0];
    await page.locator('.citation-chip').first().click();
    const drawer = findDrawer(page, '回答证据');
    await expect(drawer.getByRole('region', { name: '原始引用逐行内容' })).toContainText(fixture.marker);
    await expect(drawer.getByRole('link', { name: '查看该知识修订' })).toHaveCount(0);
    const citationLink = `/runs/${fixture.run_id}?citation=${encodeURIComponent(citation.id)}`;
    await expect(drawer.getByRole('link', { name: '打开受登录保护的引用' })).toHaveAttribute('href', citationLink);
    expect(calls.some(call => call.path === `/api/v1/runs/${fixture.run_id}/citations/${citation.id}`)).toBe(true);
    await page.screenshot({ path: '/artifacts/web-agent/channel-readonly-citation.png', fullPage: true, animations: 'disabled' });
    await page.goto(publicURL + citationLink);
    await expect(page.getByRole('region', { name: '原始引用逐行内容' })).toContainText(fixture.marker);
    await findDrawer(page, '回答证据').getByRole('button', { name: '关闭', exact: true }).click();
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Markdown 导出', exact: true }).click();
    const download = await downloading;
    await download.saveAs('/artifacts/web-agent/channel-answer.md');
    const markdown = readFileSync('/artifacts/web-agent/channel-answer.md', 'utf8');
    expect(markdown).toContain(fixture.marker);
    expect(markdown).toContain(`/runs/${fixture.run_id}?citation=`);
    const restricted = calls.filter(call =>
      call.method !== 'GET' || /^\/api\/v1\/(sessions|agents|spaces|source-snapshots|pages)(?:\/|$)/.test(call.path));
    expect(restricted, 'Channel page must not read ordinary personal context or issue writes').toEqual([]);
    await page.locator('.citation-chip').first().click();
    await expect(page.getByRole('region', { name: '原始引用逐行内容' })).toContainText(fixture.marker);
    release(); // Root now performs actual group/source revocation and exact test-asset cleanup.
    await expect(page.getByRole('region', { name: '事实' })).toHaveCount(0);
    await expect(page.getByRole('region', { name: '原始引用逐行内容' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Markdown 导出' })).toHaveCount(0);
    await page.screenshot({ path: '/artifacts/web-agent/channel-result-revoked.png', fullPage: true, animations: 'disabled' });
    record('channel-behavior', {
      fixture_id: fixture.fixture_id, model: 'deterministic_protocol_simulation',
      wecom: 'local protocol simulator with deployed channel/auth/IAM/Agent/knowledge services',
      passed: true, requests: calls,
      checks: ['Real PKCE', 'Readonly channel result without ordinary context reads', 'Exact-run citation and restored deep link', 'Authorized Markdown download', 'Live removal after actual root fixture revocation'],
    });
  } finally {
    page.off('request', observe);
    if (!released) release();
  }
});
