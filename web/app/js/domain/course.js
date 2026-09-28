// 界面上的课程词表：后端算好的单元状态（studykit/domain/curriculum.py 的 unit_state）→ 学习者看到的字和颜色。
// 状态怎么推出来只在后端；这里只管怎么显示（PRD_V2 §0.1.1 的词表）。
import { questions } from "../data/mock.js";

export const STATE = {
  queued: { label: "排队中", tone: "wait" },
  preparing: { label: "准备中", tone: "info", busy: true },
  blocked: { label: "暂时不能学", tone: "bad" },
  ask: { label: "待确认", tone: "warn" },
  ready: { label: "能学", tone: "ok" },
  learning: { label: "在学", tone: "info" },
  quiz: { label: "单元题", tone: "info" },
  done: { label: "学完", tone: "ok" },
};
export const TAGS = { new_version: "有新版本" };

export const badgeOf = u => STATE[u.state.key] || { label: u.state.key, tone: "wait" };

// 行尾的数字：一眼能读的进度
export function shortOf(u) {
  const k = u.state.key, c = u.course, q = u.quiz;
  if (k === "learning") return `${c.passed}/${c.sections} 节`;
  if (k === "ready") return `${c.sections} 节`;
  if (k === "quiz" || k === "done") return q ? `单元题 ${q.answered}/${q.questions}` : `${c.sections}/${c.sections} 节`;
  if (k === "preparing") return u.prep.progress?.label || "";
  return "";
}

// 待确认的提议（模拟：规划老师还没建）。范围没定的单元是真实的（course.yaml 的 scope: open）
export const proposalFor = id => questions.find(q => q.unit === id) || null;

// 知识点状态的理由（后端原文）→ 好读一点："tools/01-shell#q3 得分 0.666" → "单元题 q3 得分 67%"
export const reasonText = r => String(r).replace(/[a-z]+\/[\w-]+#(q\d+) 得分 ([\d.]+)/, (m, q, s) => `单元题 ${q} 得分 ${Math.round(Number(s) * 100)}%`)
  .replace(/（tutor）$/, "（导师）");

export const sectionTitle = s => String(s.title || "").replace(/^第 \d+ 节\s*/, "");

// 课程计划 → 按部分分组的节（带是否通过）
export function sectionsOf(page) {
  const passed = new Set(page.progress.passed);
  let i = 0;
  return page.plan.parts.map(p => ({ title: p.title, sections: p.sections.map(s => ({ ...s, index: i, passed: passed.has(i++) })) }));
}
export const remainingMinutes = page => sectionsOf(page).flatMap(p => p.sections).filter(s => !s.passed).reduce((a, s) => a + (s.minutes || 0), 0);

export function knowledgeOfUnit(graph, unitId) {
  const ns = graph.nodes.filter(n => (n.units || []).includes(unitId));
  return { nodes: ns, total: ns.length, mastered: ns.filter(n => n.state === "mastered").length, weak: ns.filter(n => n.state === "weak") };
}

// 一次备课运行 → 一句人话（老师 · 动态、备课记录）
const STAGE = { research: "整理讲义", outline: "写大纲", section: "写第 {i} 节", assemble: "拼成单元", repair: "按评审意见修改", plan: "整份生成（旧流程）" };
export function describeRun(r) {
  const what = (STAGE[r.stage] || r.stage).replace("{i}", (r.index ?? 0) + 1);
  const verdict = r.loop ? { accepted: "检验通过", escalated: "检验没通过，停下了" }[r.loop.verdict] || r.loop.verdict
    : r.check?.verdict === "fail" ? "自动检查没通过" : r.submitted ? "" : "没交出结果";
  return { what, verdict, published: r.published, teacher: r.stage === "research" ? "调研" : "备课" };
}
