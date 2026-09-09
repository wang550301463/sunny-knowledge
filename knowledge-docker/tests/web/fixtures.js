import { test as base, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import net from 'node:net';

const privateConfig = JSON.parse(readFileSync('/run/knowledge/test-env.json', 'utf8'));
export const publicURL = privateConfig.public_url;
export const workerID = privateConfig.ingest_principal_id;

export const test = base.extend({
  bridge: [async ({}, use) => {
    const origin = new URL(publicURL);
    if (origin.protocol !== 'http:' || origin.hostname !== 'localhost')
      throw new Error('Docker browser regression requires the isolated localhost HTTP origin');
    // Keep the real public issuer, Host and callback URL in Chromium; only TCP routing changes.
    const sockets = new Set();
    const server = net.createServer(socket => {
      const upstream = net.connect({ host: 'gateway', port: 8080 });
      for (const item of [socket, upstream]) {
        sockets.add(item);
        item.on('close', () => sockets.delete(item));
        item.on('error', () => { socket.destroy(); upstream.destroy(); });
      }
      socket.pipe(upstream); upstream.pipe(socket);
    });
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(Number(origin.port || 80), '127.0.0.1', resolve);
    });
    try { await use(true); }
    finally { for (const socket of sockets) socket.destroy(); await new Promise(r => server.close(r)); }
  }, { scope: 'worker', auto: true }],
});

export async function login(page) {
  await page.goto(publicURL);
  await page.getByRole('button', { name: '使用企业账号登录' }).click();
  try {
    await page.locator('#username').fill(privateConfig.admin_username);
    await page.locator('#password').fill(privateConfig.admin_password);
    await page.locator('#kc-login').click();
  } catch { throw new Error('Real Keycloak login form did not complete'); }
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByRole('link', { name: '知识空间', exact: true })).toBeVisible();
}

export async function api(page, method, path, body) {
  const result = await page.evaluate(async ({ method, path, body }) => {
    const key = Object.keys(sessionStorage).find(k => k.startsWith('knowledge.session.user:'));
    if (!key) return { status: 401 };
    const user = JSON.parse(sessionStorage.getItem(key));
    const response = await fetch('/api/v1' + path, {
      method, headers: { Authorization: 'Bearer ' + user.access_token, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return { status: response.status, data: await response.json() };
  }, { method, path, body });
  expect(result.status, 'Authenticated setup API status').toBeGreaterThanOrEqual(200);
  expect(result.status, 'Authenticated setup API status').toBeLessThan(300);
  return result.data;
}

export { expect };
