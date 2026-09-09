import { readFileSync } from 'node:fs';
import { test, expect, publicURL, realLogin, api, request, assets, seed, cleanup, gate, gateState, record, drawer as findDrawer, selectOption } from './fixtures.js';

test('actual PKCE, Agent publish, incremental answer, resumed SSE, citation, download and live revocation', async ({ page }) => {
  const owned = assets();
  const evidence = { fixture: 'deterministic_protocol_simulation', checks: [] };
  const streams = [];
  const observeStream = request => {
    if (/\/api\/v1\/runs\/[^/]+\/events$/.test(new URL(request.url()).pathname)) {
      const raw = request.headers()['last-event-id'];
      streams.push({ cursor: raw && /^\d+$/.test(raw) ? Number(raw) : 0 });
    }
  };
  page.on('request', observeStream);
  try {
    evidence.login = await realLogin(page);
    await seed(page, owned);

    await test.step('Create and publish through the actual Agent editor', async () => {
      await page.getByRole('link', { name: '智能体', exact: true }).click();
      await page.getByRole('button', { name: '创建智能体', exact: true }).click();
      const editor = findDrawer(page, '创建智能体');
      await editor.getByLabel('名称', { exact: true }).fill(owned.name);
      await selectOption(page, editor.getByLabel('归属空间'), owned.name);
      await selectOption(page, editor.getByLabel('Chat 模型'), `${owned.model.name} · protocol-fixture-chat · 已测试`);
      await selectOption(page, editor.getByLabel('知识范围', { exact: true }), owned.name);
      await editor.getByLabel('名称', { exact: true }).click();
      await editor.getByRole('checkbox', { name: '混合检索', exact: true }).uncheck();
      await expect(editor.getByRole('checkbox', { name: '读取证据', exact: true })).toBeChecked();
      const created = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/agents' && r.request().method() === 'POST');
      await editor.getByRole('button', { name: '保存配置', exact: true }).click();
      const response = await created;
      expect(response.status()).toBe(201);
      owned.agent = await response.json();
      expect(owned.agent.config.space_ids).toEqual([owned.space.id]);
      expect(owned.agent.config.tools).toEqual(['get']);
      expect(owned.agent.config.model_configuration_id).toBe(owned.model.configuration_id);
      const workspace = findDrawer(page, '智能体配置与发布');
      const published = page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1/agents/${owned.agent.id}/publish`);
      await workspace.getByRole('button', { name: '发布当前版本', exact: true }).click();
      expect((await published).status()).toBe(200);
      await expect(workspace.getByText('已共享', { exact: true })).toBeVisible();
      const current = await api(page, 'GET', `/agents/${owned.agent.id}`);
      expect(current.published_configuration_id).toBe(owned.agent.configuration_id);
      await page.screenshot({ path: '/artifacts/web-agent/agent-published.png', fullPage: true, animations: 'disabled' });
      evidence.checks.push('UI saved actual fixed model/scope/get tool and published actual CAS version');
    });

    await test.step('Show a validated block before the model finishes and resume the real SSE cursor', async () => {
      await page.goto(`${publicURL}/chat?agent=${owned.agent.id}&configuration=${owned.agent.configuration_id}`);
      await expect(page.getByRole('combobox', { name: '智能体', exact: true })).toBeVisible();
      await page.getByRole('textbox', { name: '问题', exact: true }).fill(owned.question);
      await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled();
      await gate('gates', owned.revision.id);
      const started = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/runs' && r.request().method() === 'POST');
      await page.getByRole('button', { name: '发送问题' }).click();
      const response = await started;
      expect(response.status()).toBe(201);
      owned.run = await response.json();
      const facts = page.getByRole('region', { name: '事实', exact: true });
      await expect(facts.getByText(owned.marker, { exact: true })).toBeVisible({ timeout: 8000 });
      expect(await gateState(owned.revision.id)).toEqual({ started: true, completed: false });
      const prefix = await api(page, 'GET', `/runs/${owned.run.id}`);
      expect(prefix.status).toBe('running');
      expect(prefix.answer_complete).toBe(false);
      expect(prefix.answer.gaps).toEqual([]);
      await expect(page.getByText('回答正在更新，以下片段已完成校验。', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: /执行步骤 ·/ }).click();
      await expect(page.getByText(/调用知识工具 · 读取证据/).first()).toBeVisible({ timeout: 2000 });
      await page.getByRole('button', { name: /执行步骤 ·/ }).click();
      await page.screenshot({ path: '/artifacts/web-agent/answer-prefix.png', fullPage: true, animations: 'disabled' });
      const cursor = await page.evaluate(id => Number(sessionStorage.getItem(`sunny:agent-run:${id}:cursor`)), owned.run.id);
      expect(cursor).toBeGreaterThan(0);
      const beforeReload = streams.length;
      await page.reload();
      await expect(facts.getByText(owned.marker, { exact: true })).toBeVisible({ timeout: 5000 });
      await expect.poll(() => streams.slice(beforeReload).some(item => item.cursor >= cursor), { timeout: 3000 }).toBe(true);
      expect(await gateState(owned.revision.id)).toEqual({ started: true, completed: false });
      await gate('releases', owned.revision.id);
      await expect(page.getByText('已完成', { exact: true })).toBeVisible();
      owned.run = await api(page, 'GET', `/runs/${owned.run.id}`);
      expect(owned.run.status).toBe('completed');
      expect(owned.run.answer_complete).toBe(true);
      expect(owned.run.configuration_id).toBe(owned.agent.configuration_id);
      expect(owned.run.actual_scope).toEqual([owned.space.id]);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      // Only the numeric cursor is stored; resumed UI stages contain subsequent
      // events, not a cached replay of the earlier authorized tool metadata.
      await expect(page.getByRole('region', { name: '证据缺口' })).toContainText('协议模拟结果');
      evidence.checks.push('Visible validated prefix while model gate was blocked', 'Actual browser SSE and Last-Event-ID resume after reload', 'Immutable Agent configuration and actual run scope');
      evidence.streams = streams;
    });

    await test.step('Read pinned evidence and download the server-authorized Markdown', async () => {
      const citation = owned.run.citations[0];
      // The exact-run endpoint is also supported for Web runs; the ordinary Web drawer
      // retains its source-snapshot route. Channel-only browser routing has a separate case.
      const snapshot = await api(page, 'GET', `/runs/${owned.run.id}/citations/${citation.id}`);
      expect(snapshot.id).toBe(citation.evidence.revision_id);
      expect(snapshot.text).toContain(owned.marker);
      await page.getByRole('button', { name: /docs\/browser-agent.md/ }).first().click();
      const drawer = findDrawer(page, '回答证据');
      await expect(drawer.getByRole('region', { name: '原始引用逐行内容' })).toContainText(owned.marker);
      await expect(drawer.getByRole('link', { name: '查看该知识修订' })).toHaveAttribute('href', new RegExp(`revision=${owned.revision.id}`));
      await page.screenshot({ path: '/artifacts/web-agent/answer-citation.png', fullPage: true, animations: 'disabled' });
      await drawer.getByRole('button', { name: '关闭', exact: true }).click();
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Markdown 导出', exact: true }).click();
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toBe('knowledge-answer.md');
      await download.saveAs('/artifacts/web-agent/answer.md');
      const markdown = readFileSync('/artifacts/web-agent/answer.md', 'utf8');
      expect(markdown).toContain(owned.marker);
      expect(markdown).toContain(citation.evidence.path);
      await page.getByRole('button', { name: /反\s*馈/, exact: true }).click();
      const feedback = page.getByRole('dialog', { name: '反馈本次回答' });
      await feedback.getByRole('textbox', { name: '反馈说明' }).fill('浏览器协议验收，不作模型质量标签。');
      const sent = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/feedback' && r.request().method() === 'POST');
      await feedback.getByRole('button', { name: '提交反馈' }).click();
      expect((await sent).status()).toBe(201);
      evidence.checks.push('Pinned protected source and exact-run citation API', 'Real browser Markdown download from authorized export endpoint', 'Actual feedback scope accepted');
    });

    await test.step('Browse the actual channel editor without configuring a bot secret', async () => {
      await page.goto(`${publicURL}/settings/channels?agent=${owned.agent.id}`);
      await expect(page.getByRole('heading', { name: '企业微信渠道', exact: true })).toBeVisible();
      await page.getByRole('button', { name: '添加企微机器人', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '添加企微机器人', exact: true });
      await expect(dialog.getByLabel('已发布智能体')).toBeVisible();
      await expect(dialog.getByLabel('BotSecret', { exact: true })).toHaveValue('');
      await expect(dialog.getByText(owned.name, { exact: true }).first()).toBeVisible();
      await page.screenshot({ path: '/artifacts/web-agent/channel-editor-unconfigured.png', fullPage: true, animations: 'disabled' });
      evidence.checks.push('Actual published Agent channel editor with no credentials or connection claim');
    });

    await test.step('Revoke only this source and hide its open answer, citation and export', async () => {
      await page.goto(`${publicURL}/runs/${owned.run.id}`);
      await expect(page.getByRole('region', { name: '事实' }).getByText(owned.marker, { exact: true })).toBeVisible();
      await page.getByRole('button', { name: /docs\/browser-agent.md/ }).first().click();
      await expect(page.getByRole('region', { name: '原始引用逐行内容' })).toContainText(owned.marker);
      await api(page, 'PUT', '/grants', {
        space_id: owned.space.id, resource_id: owned.source.resource_id,
        action: 'read', subjects: [owned.worker],
      });
      await expect(page.getByText('当前授权已变化，已停止展示此回答及其上下文。', { exact: true })).toBeVisible();
      await expect(page.getByText(owned.marker, { exact: true })).toHaveCount(0);
      await expect(page.getByRole('region', { name: '原始引用逐行内容' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Markdown 导出' })).toHaveCount(0);
      const hidden = await api(page, 'GET', `/runs/${owned.run.id}`);
      expect(hidden.content_hidden).toBe(true);
      expect(hidden).not.toHaveProperty('answer');
      for (const path of [`/runs/${owned.run.id}/export`, `/runs/${owned.run.id}/citations/${owned.run.citations[0].id}`]) {
        const denied = await request(page, 'GET', path);
        expect([403, 404]).toContain(denied.status);
        expect(JSON.stringify(denied.data)).not.toContain(owned.marker);
      }
      await page.reload();
      await expect(page.getByText(/已停止展示此回答/)).toBeVisible();
      await expect(page.getByText(owned.marker, { exact: true })).toHaveCount(0);
      await page.screenshot({ path: '/artifacts/web-agent/answer-revoked.png', fullPage: true, animations: 'disabled' });
      evidence.checks.push('Source-level live revocation clears open DOM and denies citation/export', 'Refresh cannot restore hidden content');
    });
    record('behavior', { ...evidence, passed: true });
  } finally {
    page.off('request', observeStream);
    await cleanup(page, owned);
  }
});
