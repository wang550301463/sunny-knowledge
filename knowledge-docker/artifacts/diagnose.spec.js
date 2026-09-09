import { test, publicURL } from './fixtures.js';
test('native fetch receiver', async ({page}) => {
  await page.goto(publicURL);
  const result = await page.evaluate(async () => {
    const client = { fetcher: fetch };
    try { return {status:(await client.fetcher('/healthz')).status}; }
    catch (error) { return {name:error.name, message:error.message}; }
  });
  console.log(JSON.stringify(result));
});
