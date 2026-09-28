// 真实数据：每个接口一个函数，同一次页面显示里只请求一次（缓存到 reset()）。接口都在 studykit/web.py。
import { api } from "../lib/api.js";

let cache = new Map();
const once = (key, fn) => { if (!cache.has(key)) cache.set(key, fn().catch(e => { cache.delete(key); throw e; })); return cache.get(key); };
export const reset = () => { cache = new Map(); };

export const course = () => once("course", () => api("/api/course"));                            // 阶段 → 学科 → 单元（状态算好）、下一步
export const unit = id => once(`unit:${id}`, () => api(`/api/unit?id=${encodeURIComponent(id)}`)); // 课程计划、进度、版本、先修要求
export const kg = () => once("kg", () => api("/api/kg"));                                        // 全部知识点（带状态、原因、哪个单元教）
export const studyLog = () => once("log", () => api("/api/log"));                                // 学习记录
export const profile = () => once("profile", () => api("/api/profile"));                         // 我的资料、时间偏好、老师的观察
export const prepRuns = id => once(`prep:${id}`, () => api(`/api/panel/unit?id=${encodeURIComponent(id)}`)); // 备课的每次运行
export const prepInput = id => once(`ctx:${id}`, () => api(`/api/panel/context?id=${encodeURIComponent(id)}`)); // 备课读到的输入（现在的）

async function write(path, body) {
  const out = await api(path, body);
  reset();
  return out;
}
export const switchVersion = (unitId, planId) => write("/api/unit/switch", { unit: unitId, plan: planId });
export const refuteObservation = (id, note) => write("/api/profile/refute", { id, note });
export const prepare = unitId => write("/api/panel/prepare", { unit: unitId });                // 跳课时现在就备这一课（D-064）

// 老的学习页和答题页（D-046 不改）
export const learnHref = id => `/?unit=${encodeURIComponent(id)}`;
export const quizHref = ref => `/?lesson=${encodeURIComponent(ref)}`;

// 课程里的全部单元（按学习顺序：路线上的按路线在前，其余按学科顺序，和后端 CourseDef.units 一致，D-048），每个带上所属学科
export function flatUnits(c) {
  const rank = new Map((c.path || []).map((id, i) => [id, i]));
  const at = u => rank.has(u.id) ? rank.get(u.id) : rank.size;
  return c.subjects.flatMap(s => s.units.map(u => ({ ...u, subject: s }))).sort((a, b) => at(a) - at(b));
}
