// 只存在这个浏览器里的东西：你对「待确认」的回答、你提的反馈。都是还没接入系统的功能（模拟），
// 接上以后它们是证据（只追加），这个文件就删掉。
const KEY = "cs-study-app";
const initial = () => ({ answers: {}, feedback: [] });

let state = initial();
try { state = { ...initial(), ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { /* 隐私模式等：用空状态 */ }

const listeners = new Set();
export const get = () => state;
export function update(fn) {
  fn(state);
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* 存不了就只在这次打开时有效 */ }
  listeners.forEach(l => l());
}
export const subscribe = l => { listeners.add(l); return () => listeners.delete(l); };
