import { test, expect } from '@playwright/test';

async function mockWorkspace(page) {
  const projects = [];
  const events = {};
  let fail = false;
  let dropNextChat = false;
  let nextId = 0;
  let state = { project: null, session: null, events: [], busy: false, model: 'test-model', documents: {} };
  const select = project => {
    state = {
      ...state, project, events: events[project.id] || [], busy: false,
      session: { id: 1, compact_count: 0, context_size: 3000, context_limit: 400000, context_tokens: 750, context_limit_tokens: 100000, active_request: '', todos: [] },
      documents: { project: `# ${project.name}\n\nKeep changes focused.`, handoff: '' },
    };
  };
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON();
    let data;
    if (path === '/api/state') data = state;
    else if (path === '/api/projects' && request.method() === 'GET') data = projects;
    else if (path === '/api/projects') {
      const project = { id: body.name.toLowerCase().replaceAll(' ', '-'), name: body.name, root: body.root };
      projects.push(project);
      select(project);
      data = state;
    } else if (path.endsWith('/open')) {
      select(projects.find(project => path.includes(`/${project.id}/`)));
      data = state;
    } else if (path.endsWith('/history')) data = { text: 'session_001.jsonl:12\nUse the official SDK.' };
    else if (path === '/api/chat') {
      if (dropNextChat) {
        dropNextChat = false;
        await route.abort();
        return;
      }
      state.events.push({ id: String(++nextId), kind: 'user', text: body.message || 'Continue', time: new Date().toISOString(), session: 1 });
      state.busy = true;
      state.session.active_request = body.message || state.session.active_request;
      if (fail) {
        state.events.push({ id: String(++nextId), kind: 'error', text: 'Model unavailable', time: new Date().toISOString(), session: 1 });
        state.busy = false;
        await route.fulfill({ status: 502, json: { detail: 'Model unavailable' } });
        return;
      }
      state.events.push({ id: String(++nextId), kind: 'assistant', text: '**Completed.**\n\n```python\nprint("Hello")\n```\n<img src=x onerror="window.unsafe=true">', time: new Date().toISOString(), session: 1 });
      events[state.project.id] = state.events;
      state.session.active_request = '';
      state.session.todos = [{ content: 'Verify the change', status: 'completed' }];
      state.busy = false;
      data = state;
    } else throw new Error(`Unexpected API route: ${path}`);
    await route.fulfill({ json: data });
  });
  return {
    setFailure: value => { fail = value; },
    dropNextChat: () => { dropNextChat = true; },
  };
}

async function createProject(page, name = 'Long Code', path = '/tmp/long-code') {
  await page.getByRole('button', { name: /新建项目/ }).click();
  await page.getByLabel('项目名称', { exact: true }).fill(name);
  await page.getByLabel('代码仓库路径').fill(path);
  await page.getByRole('button', { name: '接入项目' }).click();
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
}

test('create projects, chat, render Markdown safely, and keep conversations isolated', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await mockWorkspace(page);
  await page.goto('/');
  await expect(page.getByText('已连接本地服务')).toBeVisible();
  await expect(page.getByRole('button', { name: '发送消息' })).toBeDisabled();
  await createProject(page);
  await expect(page.locator('#context-tokens')).toHaveText('≈ 750 / 100,000 词元');
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('Inspect this project');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect(page.locator('.message-assistant strong').filter({ hasText: 'Completed.' })).toBeVisible();
  await expect(page.locator('.markdown pre')).toContainText('print("Hello")');
  await expect(page.locator('.todo.completed')).toContainText('Verify the change');
  expect(await page.evaluate(() => window.unsafe)).toBeUndefined();
  await createProject(page, 'Second Project', '/tmp/second');
  await expect(page.getByText('Inspect this project', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /Long Code tmp\/long-code/ }).click();
  await expect(page.getByText('Inspect this project', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('project documents, history, failed requests, and continuation', async ({ page }) => {
  const mock = await mockWorkspace(page);
  await page.goto('/');
  await createProject(page);
  await page.getByRole('button', { name: /项目概述/ }).click();
  await expect(page.locator('#document-content')).toContainText('Keep changes focused.');
  await page.locator('#document-dialog').getByRole('button', { name: '关闭对话框' }).click();
  await page.getByRole('button', { name: /搜索历史/ }).click();
  await page.getByRole('textbox', { name: '搜索关键词' }).fill('SDK');
  await page.locator('#history-form').getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page.locator('#history-results')).toContainText('Use the official SDK.');
  await page.keyboard.press('Escape');
  mock.setFailure(true);
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('Resume after an error');
  await page.getByRole('textbox', { name: '消息', exact: true }).press('Enter');
  await expect(page.getByRole('alert').first()).toHaveText('Model unavailable');
  await expect(page.getByRole('button', { name: '继续未完成的请求' })).toBeVisible();
  mock.setFailure(false);
  await page.getByRole('button', { name: '继续未完成的请求' }).click();
  await expect(page.getByRole('button', { name: '继续未完成的请求' })).toBeHidden();
  await expect(page.locator('.message-assistant')).toHaveCount(1);
});

test('mobile layout stays within the viewport and supports keyboard dialogs', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockWorkspace(page);
  await page.goto('/');
  await expect(page.getByText('已连接本地服务')).toBeVisible();
  await page.keyboard.press('n');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  await createProject(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/long-code-mobile.png', fullPage: true });
});

test('desktop workspace screenshot', async ({ page }) => {
  await mockWorkspace(page);
  await page.goto('/');
  await createProject(page);
  await page.screenshot({ path: '/tmp/long-code-desktop.png', fullPage: true });
});

test('connection errors are visible and do not enable chat', async ({ page }) => {
  await page.route('**/api/**', route => route.abort());
  await page.goto('/');
  await expect(page.getByRole('alert').first()).toContainText('本地 API 暂时不可用');
  await expect(page.getByRole('button', { name: '发送消息' })).toBeDisabled();
});

test('a draft survives a chat request that never reaches the API', async ({ page }) => {
  const mock = await mockWorkspace(page);
  await page.goto('/');
  await createProject(page);
  mock.dropNextChat();
  const composer = page.getByRole('textbox', { name: '消息', exact: true });
  await composer.fill('Keep this draft');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect(composer).toHaveValue('Keep this draft');
  await expect(page.locator('.message-user')).toHaveCount(0);
});
