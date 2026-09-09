import {
  test, expect, publicURL, realLogin, api, request, drawer, selectOption,
  output, record, assets, setup, prepareAgent, gate, cleanup,
} from './fixtures.js';

async function returnToGuide(page) {
  await page.locator('a[href="/onboarding"]').filter({ hasText: '首次使用' }).click();
  await expect(page.getByRole('heading', { name: '首次使用', exact: true })).toBeVisible();
  await expect(page.getByLabel('本次知识空间')).toBeEnabled();
}

test('real first-use guide: preview, reviewed Wiki, cited Web run, restoration and source revocation', async ({ page }) => {
  const owned = assets();
  const proof = { fixture: 'deterministic_chat_protocol_simulation', retrieval: 'not_independently_verified', checks: [] };
  const streams = [];
  const reads = [];
  const observe = req => {
    const url = new URL(req.url());
    if (/^\/api\/v1\/runs\/[^/]+\/events$/.test(url.pathname))
      streams.push(url.pathname);
    // Retain only our selected resource route identifiers, never headers, query,
    // model prompts, callback URLs, private response bodies or browser storage.
    if (owned.source && url.pathname === `/api/v1/sources/${owned.source.id}`)
      reads.push(url.pathname);
  };
  page.on('request', observe);
  try {
    proof.login = await realLogin(page);
    await setup(page, owned);

    await test.step('Actual guide discovers a probed simulation and explicitly granted space', async () => {
      await returnToGuide(page);
      await expect(page.locator('#onboarding-0')).toContainText(owned.model.name);
      await expect(page.locator('#onboarding-0')).toContainText('模拟协议模型');
      await expect(page.getByText('检索能力尚未独立验证', { exact: true })).toBeVisible();
      await page.getByLabel('本次知识空间').selectOption(owned.space.id);
      await expect(page.locator('#onboarding-1')).toContainText('当前证据已核验');
      await expect(page.getByRole('link', { name: '添加来源、预览与同步', exact: true }))
        .toHaveAttribute('href', `/spaces/${owned.space.id}?tab=sources`);
      await expect(page.locator('#onboarding-5')).not.toContainText('已核验当前 Wiki 的引用回答');
      proof.checks.push('Actual capability probe + safe discovery, explicit own-space grants, retrieval status remains unknown');
    });

    await test.step('Create, preview and synchronize Markdown through the actual source form', async () => {
      await page.getByRole('link', { name: '添加来源、预览与同步', exact: true }).click();
      await page.getByRole('button', { name: /添加来源$/ }).click();
      const form = page.getByRole('dialog', { name: '添加数据来源', exact: true });
      await form.getByLabel('来源名称').fill(owned.marker);
      await selectOption(page, form.getByLabel('来源类型'), 'Markdown 文档');
      await form.getByLabel('相对文件路径').fill(owned.path);
      await form.getByLabel('Markdown 原文').fill(`# ${owned.marker}\r\n本次首用链路保留不可变原文与审核证据。\r\n`);
      const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sources' && response.request().method() === 'POST');
      await form.getByRole('button', { name: /添加来源$/ }).click();
      const response = await created;
      expect(response.status()).toBe(201);
      owned.source = await response.json();
      expect(owned.source.space_id).toBe(owned.space.id);
      expect(owned.source.config.path).toBe(owned.path);
      await expect(form).not.toBeVisible();
      const row = page.getByRole('listitem').filter({ hasText: owned.marker });
      await row.getByRole('button', { name: /预览与同步$/ }).click();
      const source = drawer(page, owned.marker);
      await expect(source.getByRole('button', { name: /启动同步$/ })).toBeDisabled();
      const previewed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/sources/${owned.source.id}/preview`);
      await source.getByRole('button', { name: /预览原始快照$/ }).click();
      const previewResponse = await previewed;
      expect(previewResponse.status()).toBe(200);
      owned.preview = await previewResponse.json();
      expect(owned.preview.file_count).toBe(1);
      await expect(source.getByText(/预览文件 · 1 个/)).toBeVisible();
      await expect(source.getByRole('button', { name: /启动同步$/ })).toBeEnabled();
      const started = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/sources/${owned.source.id}/sync`);
      await source.getByRole('button', { name: /启动同步$/ }).click();
      const taskResponse = await started;
      expect(taskResponse.status()).toBe(202);
      owned.task = await taskResponse.json();
      await expect.poll(async () => {
        owned.task = await api(page, 'GET', `/tasks/${owned.task.id}`);
        return owned.task.status;
      }, { timeout: 150_000, intervals: [500, 1000, 2000] }).toBe('review_needed');
      expect(owned.task.result.items).toHaveLength(1);
      owned.item = owned.task.result.items[0];
      expect(owned.item.proposal_id).toBeTruthy();
      await source.getByRole('button', { name: '关闭', exact: true }).click();
      await returnToGuide(page);
      await expect(page.getByLabel('本次知识空间')).toHaveValue(owned.space.id);
      await page.getByLabel('本次数据来源').selectOption(owned.source.id);
      await expect(page.locator('#onboarding-2')).toContainText('当前证据已核验');
      await expect(page.locator('#onboarding-3')).toContainText('同步产生待审核内容');
      await page.getByLabel('本次 Wiki').selectOption(owned.item.page_id);
      await expect(page.getByRole('link', { name: '浏览已发布 Wiki', exact: true })).toHaveCount(0);
      await expect(page.getByText('所选 Wiki 已发布，可继续', { exact: true })).toHaveCount(0);
      proof.checks.push('UI created source, preview pinned actual bytes, UI sync returned real Temporal task; pending proposal did not complete guide');
    });

    await test.step('Review actual proposal, then open the current exact Wiki from restored guide', async () => {
      await page.getByRole('link', { name: '检查修订与审核', exact: true }).click();
      await selectOption(page, page.getByRole('combobox', { name: '审核空间', exact: true }), owned.name);
      const row = page.getByRole('listitem').filter({ hasText: owned.path });
      await row.getByRole('button', { name: '查看提案', exact: true }).click();
      const review = drawer(page, '审核知识变更');
      await review.getByRole('tab', { name: '修改后预览', exact: true }).click();
      await expect(review).toContainText('本次首用链路保留不可变原文与审核证据。');
      await review.getByLabel('审核意见').fill('核对本次隔离 Markdown 原文后批准；仅作协议与界面验收。');
      const approved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/reviews/${owned.item.proposal_id}/approve`);
      await review.getByRole('button', { name: '批准并发布', exact: true }).click();
      const response = await approved;
      expect(response.status()).toBe(200);
      owned.revision = await response.json();
      expect(owned.revision.proof.proposal_id).toBe(owned.item.proposal_id);
      await expect(review).not.toBeVisible();
      await returnToGuide(page);
      await expect(page.getByText('所选 Wiki 已发布，可继续', { exact: true })).toBeVisible();
      expect((await api(page, 'GET', `/tasks/${owned.task.id}`)).status).toBe('review_needed');
      const metrics = await api(page, 'GET', `/pages/${owned.item.page_id}/lifecycle?revision_id=${owned.revision.id}`);
      expect(metrics.validity.eligible).toBe(true);
      const wikiLink = page.getByRole('link', { name: '浏览已发布 Wiki', exact: true });
      await expect(wikiLink).toHaveAttribute('href', `/spaces/${encodeURIComponent(owned.space.id)}/pages/${encodeURIComponent(owned.item.page_id)}?revision=${encodeURIComponent(owned.revision.id)}`);
      await wikiLink.click();
      await expect(page.getByRole('heading', { name: owned.marker, exact: true }).first()).toBeVisible();
      await expect(page.getByText('正在查看链接指定的不可变版本', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: new RegExp(owned.path.replaceAll('.', '\\.')) }).first().click();
      const source = drawer(page, '原文引用');
      await expect(source.locator('.source-code')).toContainText(owned.marker);
      await source.screenshot({ path: `${output}/reviewed-wiki-source.png`, animations: 'disabled' });
      await source.getByRole('button', { name: '关闭', exact: true }).click();
      proof.checks.push('UI approved exact proposal; guide checked canonical eligibility and matching revision proof without rewriting original review_needed task', 'Guide link opened actual pinned Wiki and immutable source');
    });

    await test.step('Actual scoped Agent run streams a citation; guide chooses its real session and result', async () => {
      await prepareAgent(page, owned);
      await page.goto(`${publicURL}/chat?session=${owned.session.id}&agent=${owned.agent.id}&configuration=${owned.agent.configuration_id}`);
      await page.getByRole('textbox', { name: '问题', exact: true }).fill(owned.question);
      await gate('gates', owned.revision.id);
      const started = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/runs' && response.request().method() === 'POST');
      await page.getByRole('button', { name: '发送问题', exact: true }).click();
      const response = await started;
      expect(response.status()).toBe(201);
      owned.run = await response.json();
      await expect(page.getByRole('region', { name: '事实', exact: true })).toContainText(owned.marker);
      await expect.poll(() => streams.some(path => path === `/api/v1/runs/${owned.run.id}/events`)).toBe(true);
      const partial = await api(page, 'GET', `/runs/${owned.run.id}`);
      expect(partial.status).toBe('running');
      expect(partial.answer_complete).toBe(false);
      await gate('releases', owned.revision.id);
      await expect(page.getByText('已完成', { exact: true })).toBeVisible();
      owned.run = await api(page, 'GET', `/runs/${owned.run.id}`);
      expect(owned.run.actual_scope).toEqual([owned.space.id]);
      expect(owned.run.configuration_id).toBe(owned.agent.configuration_id);
      expect(owned.run.status).toBe('completed');
      expect(owned.run.answer_complete).toBe(true);
      const citation = owned.run.citations[0];
      const snapshot = await api(page, 'GET', `/runs/${owned.run.id}/citations/${citation.id}`);
      expect(snapshot.id).toBe(citation.evidence.revision_id);
      expect(snapshot.source_revision).toBe(owned.preview.source_revision);
      expect(snapshot.text).toContain(owned.marker);
      await returnToGuide(page);
      await page.getByLabel('本次问答会话').selectOption(owned.session.id);
      await expect(page.getByLabel('本次回答').locator(`option[value="${owned.run.id}"]`)).toHaveCount(1);
      await page.getByLabel('本次回答').selectOption(owned.run.id);
      await expect(page.getByText('已核验当前 Wiki 的引用回答', { exact: true })).toBeVisible();
      await expect(page.getByText('检索能力尚未独立验证', { exact: true })).toBeVisible();
      await expect(page.getByRole('link', { name: '查看所选回答', exact: true })).toHaveAttribute('href', `/runs/${owned.run.id}`);
      await page.reload();
      await expect(page.getByText('已核验当前 Wiki 的引用回答', { exact: true })).toBeVisible();
      await expect(page.getByLabel('本次知识空间')).toHaveValue(owned.space.id);
      await expect(page.getByLabel('本次回答')).toHaveValue(owned.run.id);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.locator('#onboarding-5').screenshot({ path: `${output}/cited-answer-verified.png`, animations: 'disabled' });
      await page.getByRole('link', { name: '查看所选回答', exact: true }).click();
      await expect(page.getByRole('region', { name: '事实', exact: true })).toContainText(owned.marker);
      await returnToGuide(page);
      await expect(page.getByText('已核验当前 Wiki 的引用回答', { exact: true })).toBeVisible();
      proof.checks.push('Own get-only Agent used isolated Chat protocol model; actual run SSE arrived before final completion', 'Exact run citation read, native session/run selection, guide reload and result-link return all reauthorized');
    });

    await test.step('Real source revocation withdraws derived guide metadata; local reset restores usable catalog', async () => {
      await api(page, 'PUT', '/grants', {
        space_id: owned.space.id, resource_id: owned.source.resource_id, action: 'read', subjects: [],
      });
      await expect(page.getByRole('alert').filter({ hasText: '权限不足' })).toBeVisible();
      await expect(page.getByText(owned.marker, { exact: true })).toHaveCount(0);
      await expect(page.getByText(owned.path, { exact: true })).toHaveCount(0);
      await expect(page.getByLabel('本次知识空间').locator(`option[value="${owned.space.id}"]`)).toHaveCount(0);
      await expect(page.getByText('已核验当前 Wiki 的引用回答', { exact: true })).toHaveCount(0);
      await expect(page.getByRole('link', { name: '浏览已发布 Wiki', exact: true })).toHaveCount(0);
      await expect(page.getByRole('link', { name: '查看所选回答', exact: true })).toHaveCount(0);
      for (const path of [`/sources/${owned.source.id}`, `/pages/${owned.item.page_id}`])
        expect((await request(page, 'GET', path)).status).toBe(403);
      expect((await api(page, 'GET', `/runs/${owned.run.id}`)).content_hidden).toBe(true);
      const beforeReset = reads.length;
      await page.getByRole('button', { name: '清除本次选择', exact: true }).click();
      await expect(page.getByLabel('本次知识空间')).toBeEnabled();
      await expect(page.getByLabel('本次知识空间')).toHaveValue('');
      await expect(page.getByLabel('本次问答会话')).toHaveValue('');
      expect(new URL(page.url()).search).toBe('');
      expect(reads.length).toBe(beforeReset);
      await page.getByLabel('本次知识空间').selectOption(owned.space.id);
      await expect(page.locator('#onboarding-1')).toContainText('当前证据已核验');
      await expect(page.getByLabel('本次数据来源').locator(`option[value="${owned.source.id}"]`)).toHaveCount(0);
      await expect(page.getByText('已核验当前 Wiki 的引用回答', { exact: true })).toHaveCount(0);
      await page.locator('#onboarding-1').screenshot({ path: `${output}/revoked-selection-recovered.png`, animations: 'disabled' });
      proof.checks.push('Actual source read revocation removed source/Wiki/run completion and all derived metadata', 'Clear selection did not retry denied source; authorized space catalog recovered without restoring inaccessible source');
    });
    proof.state = 'passed';
  } finally {
    page.off('request', observe);
    record('assertions', proof);
    await cleanup(page, owned);
  }
});
