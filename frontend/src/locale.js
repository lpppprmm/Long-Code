// Translate runtime UI messages without changing API values or project content.
const messages = new Map([
  ["running", "进行中"],
  ["completed", "已完成"],
  ["failed", "失败"],
  ["retrying", "正在重试"],
  ["Compacting", "正在压缩上下文"],
  ["Preparing handoff", "正在准备交接"],
  ["Compacting failed", "上下文压缩失败"],
  ["Preparing handoff failed", "准备交接失败"],
  ["Retrying summary with shorter input", "正在缩短输入并重试摘要"],
  ["Retrying tool call with smaller input", "正在缩小工具输入并重试"],
  ["Tool call exceeded MAX_TOKENS; increase it and /continue", "工具调用超过单次输出上限 MAX_TOKENS。请分段写入文件，或调整输出上限并重启后继续。"],
  ["Tool call exceeded MAX_TOKENS after 3 attempts; request retained. Continue with smaller file edits, or increase MAX_TOKENS within the model's output limit and restart.", "工具调用连续 3 次超过单次输出上限 MAX_TOKENS，请求已保留。请分段编辑文件，或在模型支持的范围内提高 MAX_TOKENS，重启后继续。"],
  ["No matching history.", "没有匹配的历史记录。"],
  ["The agent is busy. Wait for this run to finish.", "助手正忙，请等待本次运行结束。"],
  ["The selected project changed. Refresh before sending.", "所选项目已变更，请刷新后再发送。"],
  ["There is no unfinished request to continue.", "没有可继续处理的未完成请求。"],
  ["Project not found", "未找到项目"],
  ["Invalid project ID", "项目 ID 无效"],
  ["Project name must include a letter or number", "项目名称需包含至少一个英文字母或数字"],
  ["This directory is already registered", "此目录已接入工作区"],
  ["Invalid session state", "会话状态无效"],
  ["Origin is not allowed", "不允许来自此来源的请求"],
  ["Use a nonempty query and a limit from 1 to 20", "请输入搜索关键词，并将结果数量限制设为 1 至 20"],
  ["Set MODEL_ID to a model available to your Anthropic account", "请将 MODEL_ID 设置为你的 Anthropic 账户可用的模型"],
  ["Install dependencies: pip install -r requirements.txt", "请安装依赖：pip install -r requirements.txt"],
]);

const patterns = [
  [/^Running (\w+)$/, (_, tool) => `正在运行 ${tool}`],
  [/^(\w+) (completed|failed|interrupted)$/, (_, tool, status) =>
    `${tool} ${status === "interrupted" ? "已中断" : messages.get(status)}`],
  [/^Session (\d+), compact_count=(\d+)$/, (_, session, count) =>
    `会话 ${session}，已压缩 ${count} 次`],
  [/^Retrying truncated summary \(up to (\d+) tokens\)$/, (_, limit) =>
    `正在重试被截断的摘要（最多 ${limit} 个词元）`],
  [/^Project directory does not exist: (.+)$/s, (_, path) => `项目目录不存在：${path}`],
  [/^Project already exists: (.+)$/s, (_, id) => `项目已存在：${id}`],
  [/^Unknown project: (.+)$/s, (_, id) => `未知项目：${id}`],
  [/^Missing project state: (.+)$/s, (_, path) => `缺少项目状态文件：${path}`],
  [/^Local storage error: (.+)$/s, (_, detail) => `本地存储错误：${detail}`],
];

export function translateSystemText(text = "") {
  if (messages.has(text)) return messages.get(text);
  for (const [pattern, translate] of patterns) {
    if (pattern.test(text)) return text.replace(pattern, translate);
  }
  return text;
}
