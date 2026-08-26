/* 轻会议 LiteMeet - Vue 前端公共工具（ESM 版，原生 IIFE 版见 public/js/common.js） */

/* 后端地址解析（前后端分离部署）：
 * 优先级：URL 参数 ?backend= > 部署注入的 window.LM_BACKEND > 同源
 */
export const API_BASE = (() => {
  const fromUrl = new URLSearchParams(location.search).get('backend');
  const base = fromUrl || window.LM_BACKEND || location.origin;
  return String(base).replace(/\/+$/, '');
})();

export const API = {
  async request(path, options = {}) {
    const resp = await fetch(API_BASE + '/api' + path, {
      headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      ...options,
      body: options.body instanceof FormData ? options.body : (options.body ? JSON.stringify(options.body) : undefined)
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || ('请求失败 (' + resp.status + ')'));
    return data;
  },
  get(path) { return this.request(path); },
  post(path, body) { return this.request(path, { method: 'POST', body }); },
  patch(path, body) { return this.request(path, { method: 'PATCH', body }); },
  del(path) { return this.request(path, { method: 'DELETE' }); }
};

export function toast(message, type = '') {
  let wrap = document.querySelector('.toast-wrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'toast-wrap';
    wrap.setAttribute('role', 'status');
    wrap.setAttribute('aria-live', 'polite');
    wrap.setAttribute('aria-atomic', 'true');
    document.body.appendChild(wrap);
  }
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

export function fmtTime(ts) {
  const d = new Date(ts);
  const p = n => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtDuration(sec) {
  if (!sec || sec < 0) return '0:00';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const p = n => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

export function fmtBytes(n) {
  if (!n) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

export function getParam(name) {
  return new URLSearchParams(location.search).get(name);
}

/* WebSocket 地址（http(s) 转 ws(s)，连后端业务信令端点 /ws） */
export function backendWsUrl() {
  return API_BASE.replace(/^http/, 'ws') + '/ws';
}

/* 极简 Markdown 渲染（用于 AI 纪要展示） */
export function renderMarkdown(md) {
  const lines = String(md || '').split(/\r?\n/);
  let html = '';
  let inList = false;
  const inline = (s) => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>');

  for (let raw of lines) {
    const line = raw.trimEnd();
    const listMatch = line.match(/^\s*[-*]\s+(.*)$/) || line.match(/^\s*\d+\.\s+(.*)$/);
    if (/^#{1,6}\s+/.test(line)) {
      if (inList) { html += '</ul>'; inList = false; }
      const level = line.match(/^#+/)[0].length;
      html += `<h${Math.min(level + 1, 5)}>${inline(line.replace(/^#+\s+/, ''))}</h${Math.min(level + 1, 5)}>`;
    } else if (listMatch) {
      if (!inList) { html += '<ul>'; inList = true; }
      html += `<li>${inline(listMatch[1])}</li>`;
    } else if (line.trim() === '') {
      if (inList) { html += '</ul>'; inList = false; }
    } else {
      if (inList) { html += '</ul>'; inList = false; }
      html += `<p>${inline(line)}</p>`;
    }
  }
  if (inList) html += '</ul>';
  return html;
}

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function getUserName() {
  return localStorage.getItem('litemeet.name') || '';
}

export function setUserName(name) {
  localStorage.setItem('litemeet.name', name);
}

export function getUserId() {
  let id = localStorage.getItem('litemeet.uid');
  if (!id) {
    id = 'u_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    localStorage.setItem('litemeet.uid', id);
  }
  return id;
}