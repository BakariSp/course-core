// 建 DOM 的最小工具：h(标签, 属性, ...子节点)。null / false 的子节点会被跳过。
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, kids);
  return el;
}

export function append(el, kids) {
  for (const kid of [kids].flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

export const fill = (el, ...kids) => { el.replaceChildren(); return append(el, kids); };

// 行内 Markdown：只处理链接和 `代码`（课程清单里的格子就是这样写的）。先转义再替换。
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export function inlineMd(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1 ↗</a>');
}
export const md = (tag, s, attrs = {}) => h(tag, { ...attrs, html: inlineMd(s) });

export const fmtMinutes = m => m >= 60 ? `${Math.floor(m / 60)} 小时${m % 60 ? ` ${Math.round(m % 60)} 分钟` : ""}` : `${Math.round(m)} 分钟`;
