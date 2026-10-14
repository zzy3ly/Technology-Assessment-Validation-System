// Production is same-origin; an explicit backend URL also supports CRA/Docker.
const BASE = (process.env.REACT_APP_BACKEND_URL || '').replace(/\/$/, '');
export const apiUrl = (path) => `${BASE}/api${path}`;

export async function request(path, { signal, ...options } = {}) {
  const response = await fetch(apiUrl(path), {
    ...options, signal,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    const message = Array.isArray(detail) ? detail.map(x => x.msg).join('；') : detail;
    throw new Error(message || `请求失败 (${response.status})`);
  }
  return data;
}

export const terminal = (status) => ['succeeded', 'failed', 'stopped', 'interrupted'].includes(status);
export const labels = {
  queued: '等待执行', running: '执行中', stopping: '正在停止', succeeded: '已完成',
  failed: '未完成', stopped: '已停止', interrupted: '已中断', received: '已接收',
  planning: '规划中', ready: '准备执行', retrying: '重试中', replanning: '重新规划',
};
