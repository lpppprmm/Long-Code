import { translateSystemText } from './locale.js';

export const API_URL = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

export async function request(path, body) {
  const response = await fetch(`${API_URL}/api${path}`, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {
    throw new Error('无法连接本地 API，请确认服务已启动。');
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg).join('; ')
      : data.detail;
    throw new Error(translateSystemText(detail) || `请求失败（${response.status}）`);
  }
  return data;
}
