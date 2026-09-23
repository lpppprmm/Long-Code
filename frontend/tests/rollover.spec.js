import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

test('real API recovers a truncated summary and rolls over twice', async ({ page, request }) => {
  const api = 'http://127.0.0.1:8765';
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', { name: /Rollover demo/ }).click();
  await page.getByRole('textbox', { name: '消息', exact: true }).fill(
    'Complete the offline rollover demo. Preserve DEMO_REQUIREMENT.',
  );
  await page.getByRole('button', { name: '发送消息' }).click();

  for (const [phase, label, session] of [
    ['Compacting', '正在压缩上下文', 1], ['Preparing handoff', '正在准备交接', 1],
    ['Compacting', '正在压缩上下文', 2], ['Preparing handoff', '正在准备交接', 2],
  ]) {
    await expect(page.locator('#run-status')).toHaveText(label);
    await expect(page.locator('#session-number')).toHaveText(String(session).padStart(2, '0'));
    const state = await (await request.get(`${api}/api/state`)).json();
    expect(state.busy).toBe(true);
    expect(state.phase).toBe(phase);
    expect(state.session.active_request).toContain('Complete the offline rollover demo');
    expect(state.documents.project).toContain('DEMO_REQUIREMENT');
    await request.post(`${api}/__demo__/advance`);
  }

  await expect(page.locator('.message-assistant')).toContainText('Requirements retained through Session 3');
  await expect(page.locator('#session-number')).toHaveText('03');
  await expect(page.locator('#run-status')).toHaveText('就绪');
  await expect(page.locator('#continue')).toBeHidden();
  await expect(page.locator('.activity-body').filter({ hasText: '正在重试被截断的摘要' })).toBeVisible();
  await expect(page.locator('.activity-body code').filter({ hasText: 'agent://PROJECT.md' })).toHaveCount(2);
  const completed = page.locator('.activity-event[data-status="completed"]').filter({ hasText: 'artifact-4.txt' });
  await completed.getByText('结果预览').click();
  await expect(completed.locator('pre')).toContainText('Wrote');
  await page.getByRole('button', { name: /会话交接/ }).click();
  await expect(page.locator('#document-content')).toContainText('DEMO_REQUIREMENT');
  await expect(page.locator('#document-content')).toContainText('Verify demo output');
  await page.locator('#document-dialog').getByRole('button', { name: '关闭对话框' }).click();
  await page.screenshot({ path: '/tmp/long-code-rollover-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '/tmp/long-code-rollover-mobile.png', fullPage: true });

  const state = await (await request.get(`${api}/api/state`)).json();
  expect(state.session.context_limit_tokens).toBe(4000);
  expect(state.session.compact_count).toBe(0);
  expect(state.phase).toBeNull();
  const rollover = state.events.filter(event => event.activity === 'rollover' && event.status === 'completed');
  expect(rollover.map(event => event.session)).toEqual([2, 3]);
  expect(state.events.find(event => event.activity === 'summary').max_tokens).toBe(4000);
  const data = join(dirname(state.project.root), 'data', 'projects', 'rollover-demo');
  for (const id of ['001', '002']) {
    expect(await readFile(join(data, 'handoffs', `session_${id}.md`), 'utf8')).toContain('DEMO_REQUIREMENT');
  }
  const checkpoint = JSON.parse(await readFile(join(data, 'state.json'), 'utf8'));
  expect(checkpoint.current_session).toBe(3);
  expect(checkpoint.active_request).toBe('');
  const saved = (await readFile(join(data, 'web-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  expect(saved).toEqual(state.events);
  expect(errors).toEqual([]);
});
