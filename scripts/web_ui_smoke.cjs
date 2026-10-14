/* Real browser smoke test. --live explicitly submits one short paid model call. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

(async () => {
  const output = path.resolve(__dirname, '../.web-runtime/screenshots');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_EXECUTABLE_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  });
  const checks = [];
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.WEB_URL || 'http://127.0.0.1:7777');
    await page.getByText('本地服务已连接', { exact: false }).waitFor();
    await page.getByRole('heading', { name: /从一个问题/ }).waitFor();
    await page.screenshot({ path: path.join(output, 'workspace-desktop.png'), fullPage: true });
    checks.push('desktop workspace and API connection');
    await page.getByRole('button', { name: /Skill 库/ }).click();
    await page.getByRole('heading', { name: 'test-driven-code-repair', exact: true }).waitFor();
    await page.getByRole('button', { name: /在任务中使用/ }).click();
    assert.equal(await page.getByLabel('选择 Skill').inputValue(), 'test-driven-code-repair');
    checks.push('registry to UI to selected Skill');
    await page.getByRole('button', { name: /断点恢复/ }).click();
    await page.getByRole('heading', { name: '从中断处，继续。' }).waitFor();
    checks.push('checkpoint library');
    await page.getByRole('button', { name: '任务记录', exact: true }).click();
    await page.getByRole('heading', { name: '任务记录', exact: true }).waitFor();
    const savedTasks = page.getByRole('button', { name: /查看任务/ });
    if (await savedTasks.count()) {
      await savedTasks.first().click();
      await page.getByRole('heading', { name: '任务详情' }).waitFor();
      await page.getByRole('tab', { name: '工具结果' }).click();
      await page.getByRole('tab', { name: '校验与恢复' }).click();
      await page.getByRole('tab', { name: '工作流' }).click();
      await page.screenshot({ path: path.join(output, 'workflow-desktop.png'), fullPage: true });
      checks.push('persisted task history and all execution tabs');
    }
    await page.getByRole('button', { name: '切换深色主题' }).click();
    await page.screenshot({ path: path.join(output, 'workspace-dark.png'), fullPage: true });
    await page.getByRole('button', { name: '切换浅色主题' }).click();
    checks.push('light and dark themes');
    if (process.argv.includes('--live')) {
      await page.getByRole('button', { name: /新建任务/ }).click();
      await page.getByLabel('选择 Skill').selectOption('');
      await page.getByLabel('执行方式').selectOption('casual');
      await page.getByLabel('任务内容').fill('浏览器端到端联调：请只回答「网页提交与结果展示正常」。');
      await page.getByRole('button', { name: /开始任务/ }).click();
      await page.getByRole('heading', { name: '任务详情' }).waitFor();
      await page.locator('.answer-card').getByText('网页提交与结果展示正常', { exact: false }).waitFor({ timeout: 120000 });
      assert.equal(await page.locator('.task-title .status').innerText(), '已完成');
      await page.reload();
      await page.locator('.answer-card').getByText('网页提交与结果展示正常', { exact: false }).waitFor();
      checks.push('live DeepSeek submission, completion and refresh persistence');
      await page.screenshot({ path: path.join(output, 'task-desktop.png'), fullPage: true });
    }
    await page.getByRole('button', { name: /新建任务/ }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('heading', { name: /从一个问题/ }).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(output, 'workspace-mobile.png'), fullPage: true });
    checks.push('390px responsive layout without horizontal overflow');
    assert.deepEqual(errors, []);
    checks.push('no browser JavaScript errors');
    const report = { checks, browser_errors: errors, screenshots: output, created_at: new Date().toISOString() };
    const reportName = process.argv.includes('--live') ? 'ui-smoke-live.json' : 'ui-smoke.json';
    fs.writeFileSync(path.resolve(output, '..', reportName), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
