// 课程（首页）。
//   一级：继续学习（唯一的主按钮）
//   二级：课程目录（按路线排的单元：状态 · 名称 · 学科 · 服务哪条能力 · 进度数字）；待确认（一行）
//   收起：终点（每条能力：能做到什么、怎么验收）；不在路线上的单元（按学科分组，带学科的目标与主课）
//   别处：课程设置
import { h, md } from "../lib/dom.js";
import { badge, mock } from "../components/ui.js";
import * as src from "../data/source.js";
import { badgeOf, shortOf, sectionTitle, TAGS } from "../domain/course.js";

export async function coursePage() {
  const c = await src.course();
  return h("div", { class: "stack-lg" }, continueCard(c), catalog(c));
}

// 下一步由后端推理（studykit/domain/curriculum.py 的 next_steps），每条带理由
function continueCard(c) {
  const [first, ...rest] = c.next.map(n => ({ ...n, ...target(n) }));
  if (!first) return null;
  return h("section", { class: "panel panel--hero stack-sm", "aria-labelledby": "continue-h" },
    h("div", { class: "eyebrow", id: "continue-h" }, "继续学习"),
    h("div", { class: "continue" },
      h("div", {}, h("h1", {}, first.title), h("div", { class: "muted" }, first.what), h("div", { class: "xs muted", title: "为什么推荐这个" }, first.reason)),
      h("a", { class: "btn btn--primary", href: first.href }, first.action)),
    rest.length ? h("div", { class: "small muted" }, "接下来：",
      rest.slice(0, 2).map((s, i) => [i ? " · " : "", h("a", { href: s.href }, `${s.title} ${s.short}`)])) : null);
}

function target(n) {
  if (n.kind === "quiz") return { what: `单元题 · ${n.quiz.questions} 道`, short: "单元题", href: src.quizHref(n.quiz.ref), action: "做单元题" };
  const s = n.section;
  return { what: s ? `第 ${s.index + 1} 节 · ${sectionTitle(s)}${s.minutes ? ` · ${s.minutes} 分钟` : ""}` : "",
           short: s ? `第 ${s.index + 1} 节` : "", href: src.learnHref(n.unit), action: n.kind === "start" ? "开始学" : "继续学" };
}

// 终点（D-048）：学完能做到什么、怎么验收。收起，只露一行进度
function destination(c) {
  if (!c.destination?.length) return null;
  const onPath = src.flatUnits(c).filter(u => c.path.includes(u.id));
  const done = onPath.filter(u => u.state.key === "done").length;
  return h("details", { class: "panel stack-sm" },
    h("summary", { style: "cursor:pointer" }, h("strong", {}, "终点"),
      h("span", { class: "small muted num" }, ` · ${c.destination.length} 条能力 · 路线 ${done}/${onPath.length} 个单元学完`)),
    h("dl", { class: "kv", style: "grid-template-columns:3em minmax(0,1fr)" }, c.destination.map(x => [
      h("dt", {}, h("span", { class: "tag tag--accent" }, x.id)),
      h("dd", {}, h("div", {}, x.can), h("div", { class: "small muted" }, "验收：", x.accept))])));
}

function catalog(c) {
  const units = src.flatUnits(c);
  const asks = units.filter(u => u.state.key === "ask");
  const hasPath = (c.path || []).length > 0;
  const onPath = units.filter(u => c.path?.includes(u.id));
  const later = c.subjects.map(s => ({ ...s, units: s.units.filter(u => !c.path?.includes(u.id)) })).filter(s => s.units.length);
  return h("section", { class: "stack-sm", "aria-labelledby": "catalog-h" },
    h("div", { class: "section-h" }, h("h2", { id: "catalog-h" }, "课程目录"), h("span", { class: "spacer" }), h("a", { class: "small", href: "#settings" }, "课程设置")),
    asks.length ? h("div", { class: "panel panel--warn row", style: "padding-block:var(--sp-2)" },
      h("span", { class: "badge badge--warn" }, `待确认 ${asks.length}`),
      asks.map((u, i) => [i ? h("span", { class: "muted" }, "·") : null, h("a", { href: `#q.${u.id}` }, `${u.title} · 范围`)])) : null,
    destination(c),
    hasPath ? h("div", { class: "list" }, onPath.map(u => unitRow(u, { subject: true, caps: true }))) : null,
    hasPath ? h("h3", { class: "small muted", style: "margin-top:var(--sp-3)" }, "不在路线上：用到再学") : null,
    h("div", {}, (hasPath ? later : c.subjects).map(subjectGroup)));
}

function subjectGroup(s) {
  const active = s.units.some(u => !["queued", "ask"].includes(u.state.key));
  const learned = s.units.filter(u => u.course).length;
  return h("details", { class: "group", open: active || undefined },
    h("summary", {}, h("div", { class: "group-title" },
      s.priority ? h("span", { class: "prio" }, s.priority) : null,
      h("h3", {}, s.title),
      h("span", { class: "small muted num" }, learned ? `${learned}/${s.units.length} 单元`
        : `${s.units.length} 单元 · ${s.units.some(u => u.state.key === "ask") ? "待确认" : "排队中"}`))),
    h("div", { class: "group-body stack-sm" },
      h("div", { class: "list" }, s.units.map(unitRow)),
      h("details", { class: "small" }, h("summary", { class: "muted", style: "cursor:pointer" }, "目标与主课"),
        h("dl", { class: "kv", style: "margin-top:6px" },
          h("dt", {}, "目标"), h("dd", {}, s.goal || "—"),
          h("dt", {}, "看哪部分"), h("dd", {}, s.scope || "—"),
          h("dt", {}, "主课"), h("dd", {}, s.sources.map((x, i) => [i ? "；" : "", h("a", { href: x.url, target: "_blank", rel: "noopener" }, x.title + " ↗")])),
          s.project_links.length ? [h("dt", {}, "在你的项目里"), h("dd", {}, s.project_links.map((x, i) => [i ? "；" : "", md("span", `\`${x.where}\` → ${x.concept}`)]))] : null))));
}

function unitRow(u, { subject = false, caps = false } = {}) {
  const act = rowAction(u);
  const b = badgeOf(u);
  return h("div", { class: "item" },
    badge(b),
    h("div", { class: "row", style: "gap:6px" },
      h("a", { class: "item-title", href: `#u.${u.id}` }, u.title),
      subject ? h("span", { class: "small muted" }, u.subject.title) : null,
      h("span", { class: "small muted num" }, shortOf(u)),
      ...(caps ? u.serves || [] : []).map(id => h("span", { class: "tag", title: "服务终点的这条能力" }, id)),
      ...u.state.tags.map(t => h("span", { class: "tag tag--accent" }, TAGS[t] || t)),
      u.state.key === "blocked" ? mock("自动续跑、换策略重试、上报开发者是第二期；现在由开发者处理") : null),
    act ? h("a", { class: "btn btn--sm", href: act.href }, act.label) : h("span"));
}

function rowAction(u) {
  const k = u.state.key;
  if (k === "ask") return { label: "确认", href: `#q.${u.id}` };
  if (k === "quiz") return { label: "做单元题", href: src.quizHref(u.quiz.ref) };
  if (k === "learning") return { label: "继续学", href: src.learnHref(u.id) };
  if (k === "ready") return { label: "开始学", href: src.learnHref(u.id) };
  if (k === "done") return { label: "复习", href: src.learnHref(u.id) };
  return null;
}
