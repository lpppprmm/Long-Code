// Capture the real UI using deterministic, offline example data.
// Optional: README_FONT_PATH=/path/to/NotoSansCJKsc-Regular.otf node frontend/scripts/capture-readme.mjs
import { mkdir, readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import { marked } from 'marked';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const images = resolve(root, 'docs/images');
const font = process.env.README_FONT_PATH ? await readFile(process.env.README_FONT_PATH) : null;
const project = { id: 'parser-lab', name: 'Parser Lab', root: '/workspace/parser-lab' };
const taskDocument = `# 任务记录

状态：进行中

## 下一步

为解析器的空输入分支补充回归用例，再验证修复。

验证方式：先运行空输入用例，再运行解析器相关测试。

相关文件：src/parser.py、tests/test_parser.py

## 有效约束

保持现有公开接口，不引入额外依赖。

来源：request-0001

## 关键决策

将空输入处理集中在解析入口，复用现有错误类型。

来源：evidence-7bbf3c3edcd84e8b8b59c90f414d236e

## 发现与判断

待验证：空字符串可能在分词之前触发索引越界。

## 最近的执行证据

read_file（已执行）：src/parser.py

记录中的判断需要结合原文和实际文件核对。`;

const event = (id, kind, text, extra = {}) => ({
  id: String(id), kind, text, time: '2026-01-01T02:30:00Z', session: 3, ...extra,
});
const state = {
  project, busy: false, phase: null, model: 'offline-demo',
  session: {
    id: 3, compact_count: 0, active_request: '修复空输入时解析器崩溃的问题，保持现有公开接口。',
    context_size: 46320, context_limit: 400000, context_tokens: 11580,
    context_limit_tokens: 100000, last_input_tokens: 0, last_output_tokens: 0,
    total_input_tokens: 0, total_output_tokens: 0,
    todos: [
      { content: '检查解析入口与现有错误处理', status: 'completed' },
      { content: '补充空输入回归用例', status: 'in_progress' },
      { content: '运行解析器测试并核对结果', status: 'pending' },
    ],
  },
  documents: {
    project: '# Parser Lab\n\n保持公开接口，复用现有实现。', task: taskDocument,
    handoff: '# Session Handoff\n\n已检查解析入口。下一步补充空输入用例，并验证修复。',
  },
  events: [
    event(1, 'user', '修复空输入时解析器崩溃的问题，保持现有公开接口，不引入额外依赖。'),
    event(2, 'assistant', '我会先检查解析入口和现有测试，再补充一个能复现问题的用例。'),
    event(3, 'activity', 'read_file completed', {
      activity: 'tool', tool: 'read_file', status: 'completed', target: 'src/parser.py',
      duration_ms: 18, output: 'Read the parser entry point and existing error handling.',
    }),
    event(4, 'activity', 'task_update completed', {
      activity: 'tool', tool: 'task_update', status: 'completed', duration_ms: 12,
      output: 'Saved the user constraint, source reference, and next action.',
    }),
    event(5, 'activity', 'Session 3, compact_count=0', {
      activity: 'rollover', status: 'completed', duration_ms: 420,
    }),
    event(6, 'assistant', '**已接续上一会话的任务。**\n\n原始请求、接口约束和下一步已保留。\n\n**下一步**：补充空输入回归用例，检查入口边界处理，再运行解析器相关测试。'),
  ],
};

await mkdir(images, { recursive: true });
const server = await createServer({
  root: resolve(root, 'frontend'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 5180, strictPort: true },
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1.5,
    locale: 'zh-CN', timezoneId: 'Asia/Shanghai',
  });
  const page = await context.newPage();
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/__readme_asset__/')) {
      const name = url.pathname.split('/').at(-1);
      if (!['hero.svg', 'session-lifecycle.svg', 'workspace.png', 'task-record.png'].includes(name))
        return route.abort();
      return route.fulfill({ body: await readFile(resolve(images, name)),
        contentType: name.endsWith('.svg') ? 'image/svg+xml' : 'image/png' });
    }
    if (url.pathname === '/__readme_font__' && font)
      return route.fulfill({ body: font, contentType: 'font/otf' });
    if (url.pathname.startsWith('/api/')) {
      const data = url.pathname === '/api/projects' ? [project] : state;
      return route.fulfill({ json: data });
    }
    if (url.hostname === '127.0.0.1') return route.continue();
    return route.abort(); // No model calls, external fonts, or other remote requests.
  });
  await page.goto('http://127.0.0.1:5180/', { waitUntil: 'networkidle' });
  if (font) {
    await page.addStyleTag({ content: `
      @font-face { font-family: 'Readme CJK'; src: url('/__readme_font__'); font-weight: 100 900; }
      :root, button, input, textarea, h1, h2, h3, .session-value small { font-family: Arial, 'Readme CJK', sans-serif !important; }
    ` });
  }
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('heading', { name: 'Parser Lab', exact: true }).waitFor();
  await page.evaluate(() => { document.querySelector('#conversation').scrollTop = 0; });
  await page.screenshot({ path: resolve(images, 'workspace.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: /任务记录/ }).click();
  await page.locator('#document-dialog').screenshot({ path: resolve(images, 'task-record.png'), animations: 'disabled' });

  // Render the local SVGs and a Markdown preview for visual review, outside the repo.
  for (const [name, height] of [['hero', 420], ['session-lifecycle', 440]]) {
    await page.setViewportSize({ width: 1280, height });
    await page.setContent(`<style>body{margin:0}img{display:block;width:1280px}</style><img src="http://127.0.0.1:5180/__readme_asset__/${name}.svg">`);
    await page.locator('img').evaluate(img => img.decode());
    await page.screenshot({ path: `/tmp/long-code-readme-${name}.png`, animations: 'disabled' });
  }
  const readme = (await readFile(resolve(root, 'README.md'), 'utf8'))
    .replace(/docs\/images\//g, 'http://127.0.0.1:5180/__readme_asset__/')
    .replace(/<p align="center">\s*<a href="https:\/\/github.com[\s\S]*?<\/p>/, '');
  await page.setViewportSize({ width: 1100, height: 1000 });
  await page.setContent(`<style>
    body{margin:0;background:#fff;color:#1f2328;font:16px/1.6 Arial,sans-serif}
    main{max-width:900px;margin:32px auto;padding:32px;border:1px solid #d1d9e0;border-radius:6px}
    img{max-width:100%;height:auto}h2{font-size:24px;border-bottom:1px solid #d8dee4;padding-bottom:8px;margin-top:32px}
    a{color:#0969da;text-decoration:none}table{border-collapse:collapse;width:100%;font-size:14px}
    td,th{border:1px solid #d1d9e0;padding:8px 13px;text-align:left}tr:nth-child(2n){background:#f6f8fa}
    pre{background:#f6f8fa;border-radius:6px;padding:16px;overflow:auto;font-size:13px}code{font-size:85%;background:#eff1f3;border-radius:4px;padding:2px 4px}
    pre code{padding:0;background:none}blockquote{border-left:4px solid #d1d9e0;padding-left:16px;color:#59636e}
    hr{height:1px;border:0;background:#d1d9e0;margin:28px 0}summary{cursor:pointer}sub{color:#59636e;font-size:12px}
  </style><main>${marked.parse(readme)}</main>`);
  await page.locator('img').evaluateAll(images => Promise.all(images.map(img => img.decode().catch(() => {}))));
  await page.screenshot({ path: '/tmp/long-code-readme-preview.png', fullPage: true, animations: 'disabled' });
  await page.screenshot({ path: '/tmp/long-code-readme-preview-top.png', animations: 'disabled' });
  console.log('Saved docs/images/workspace.png and docs/images/task-record.png');
  console.log('Visual review files: /tmp/long-code-readme-{hero,session-lifecycle,preview}.png');
} finally {
  await browser?.close();
  await server.close();
}
