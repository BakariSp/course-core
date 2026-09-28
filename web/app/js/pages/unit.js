// 单元页。
//   一级：单元名 · 状态 · 主按钮；我的进度（一行）；大纲
//   二级：先修要求（必须 / 更好 × 满足 / 不满足，后端算好）；学习目标
//   收起：资料；版本
//   别处：为什么这样教 · 备课记录 → 老师页
import { h, fmtMinutes } from "../lib/dom.js";
import { badge, mock, toast } from "../components/ui.js";
import { challengeButton } from "../components/challenge.js";
import * as src from "../data/source.js";
import { badgeOf, TAGS, proposalFor, sectionsOf, remainingMinutes, sectionTitle, reasonText } from "../domain/course.js";

export async function unitPage(id, rerender) {
  const c = await src.course();
  const units = src.flatUnits(c), u = units.find(x => x.id === id);
  if (!u) return h("p", {}, "没有这个单元。");
  const k = u.state.key;
  const head = (act, progress) => h("div", { class: "stack-sm" },
    h("a", { class: "small", href: "#course" }, `← 课程 · ${u.subject.title}`),
    h("div", { class: "continue" },
      h("div", { class: "stack-sm", style: "gap:4px" },
        h("div", { class: "row" }, h("h1", {}, u.title), badge(badgeOf(u)),
          ...u.state.tags.map(t => h("span", { class: "tag tag--accent" }, TAGS[t] || t)),
          k === "blocked" ? mock("自动续跑、换策略重试、上报开发者是第二期；现在由开发者处理") : null),
        progress),
      act));
  if (!u.course) return h("div", { class: "stack-lg" }, head(null, null), notReady(u, units), footer(u));

  const page = await src.unit(id);
  const quiz = u.quiz;
  const act = k === "quiz" ? h("a", { class: "btn btn--primary", href: src.quizHref(quiz.ref) }, "做单元题")
    : h("a", { class: "btn btn--primary", href: src.learnHref(id) }, k === "done" ? "复习" : k === "ready" ? "开始学" : "继续学");
  const left = remainingMinutes(page);
  const progress = h("div", { class: "sum" },
    h("span", {}, h("b", {}, `${u.course.passed}/${u.course.sections}`), " 节"),
    h("span", {}, "单元题 ", h("b", {}, quiz ? `${quiz.answered}/${quiz.questions}` : "—")),
    left ? h("span", {}, "还剩 ", h("b", {}, fmtMinutes(left))) : null);
  return h("div", { class: "stack-lg" },
    head(act, progress),
    outline(u, page),
    h("div", {}, prereqFold(page.readiness, units), goalsFold(page), sourcesFold(u, page), versionFold(u, page, rerender)),
    footer(u));
}

function outline(u, page) {
  const current = sectionsOf(page).flatMap(p => p.sections).find(s => !s.passed)?.index;
  return h("section", { class: "stack-sm", "aria-labelledby": "outline-h" },
    h("h2", { id: "outline-h" }, "大纲"),
    h("div", { class: "list" }, sectionsOf(page).map(p => h("div", { class: "outline-part" },
      h("div", { class: "xs muted" }, p.title),
      p.sections.map(s => h("div", { class: `sec reveal${s.passed ? " passed" : ""}${s.index === current ? " current" : ""}` },
        h("span", { class: "n" }, s.passed ? `✓ ${s.index + 1}` : `${s.index + 1}`),
        h("span", { class: "t" }, sectionTitle(s)),
        h("span", { class: "m" }, s.minutes ? `${s.minutes} 分钟` : ""),
        challengeButton({ kind: "section", id: `${u.id}#${s.index + 1}`, label: `${u.title} · 第 ${s.index + 1} 节 · ${sectionTitle(s)}` },
          ["讲得太跳", "太简单", "练习做不了", "内容有错"])))))));
}

function fold(id, title, summary, body, open = false) {
  return h("details", { class: "fold", id, open: open || undefined },
    h("summary", {}, h("h2", {}, title), summary), h("div", { class: "fold-body" }, body));
}

// 先修要求（D-047）：后端 readiness 按 必须 / 更好 × 满足 / 不满足 分好；每条带"在哪个单元学""谁说的"
function prereqFold(r, units) {
  const titleOf = id => units.find(u => u.id === id)?.title || id;
  const learned = r.learn.filter(n => n.state === "mastered").length;
  const unmetRow = n => h("div", { class: "item reveal", style: "grid-template-columns:minmax(0,1fr) auto auto" },
    h("span", {}, h("b", {}, n.title), h("span", { class: "small muted" }, n.state === "weak" ? " · 薄弱" : " · 没学过"),
      n.why?.length ? h("div", { class: "xs muted" }, n.why.map(reasonText).join("；")) : null),
    n.taught_in[0] ? h("a", { class: "small", href: `#u.${n.taught_in[0]}` }, `在「${titleOf(n.taught_in[0])}」`) : h("span", { class: "xs muted" }, "没有单元教它"),
    challengeButton({ kind: "node", id: n.id, label: n.title }, ["其实我会了", "这不是先修"], "其实我会了"));
  const group = (label, list, body) => list.length ? h("div", { class: "stack-sm", style: "gap:4px" }, h("div", { class: "eyebrow" }, label), body) : null;
  const chips = list => h("div", { class: "chips" }, list.map(n => h("span", { class: "chip chip--ok", title: n.by ? `谁说它是先修：${n.by}` : "" }, "✓ ", n.title)));
  const unmet = r.required.unmet.length;
  return fold("prereq", "先修要求",
    h("span", { class: "sum" },
      h("span", {}, "已满足 ", h("b", {}, r.required.met.length)),
      h("span", { class: unmet ? "bad" : "" }, "不满足 ", h("b", {}, unmet)),
      r.helpful.met.length + r.helpful.unmet.length ? h("span", {}, "先会更好 ", h("b", {}, r.helpful.met.length + r.helpful.unmet.length)) : null,
      h("span", {}, "本单元要学 ", h("b", {}, r.learn.length), `（已掌握 ${learned}）`)),
    h("div", { class: "stack" },
      !r.required.met.length && !unmet ? h("p", { class: "small muted" }, "没有必须先会的。") : null,
      group("不满足", r.required.unmet, h("div", { class: "list" }, r.required.unmet.map(unmetRow))),
      group("已满足", r.required.met, chips(r.required.met)),
      group("先会更好（不会也能学）", [...r.helpful.unmet, ...r.helpful.met], h("div", { class: "chips" },
        [...r.helpful.unmet, ...r.helpful.met].map(n => h("span", { class: `chip ${n.state === "mastered" ? "chip--ok" : ""}` }, n.state === "mastered" ? "✓ " : "", n.title)))),
      group("本单元要学", r.learn, h("div", { class: "chips" }, r.learn.map(n =>
        h("span", { class: `chip ${n.state === "mastered" ? "chip--ok" : n.state === "weak" ? "chip--weak" : ""}` }, n.state === "mastered" ? "✓ " : "", n.title))))),
    unmet > 0);
}

function goalsFold(page) {
  const o = page.plan.outcomes || [];
  return fold("goals", "学习目标", h("span", { class: "sum" }, h("span", {}, h("b", {}, o.length), " 条")),
    h("ol", { class: "stack-sm", style: "gap:4px" }, o.map(x => h("li", {}, x))));
}

function sourcesFold(u, page) {
  const later = page.plan.later || [];
  return fold("sources", "资料", h("span", { class: "sum" }, h("span", {}, `出处 ${page.sources.length} · 以后再学 ${later.length}`)),
    h("div", { class: "stack-sm" },
      u.sources.length ? [h("div", { class: "eyebrow" }, "主课里的这一部分"),
        h("ul", { class: "small" }, u.sources.map(s => h("li", {}, h("a", { href: s.url, target: "_blank", rel: "noopener" }, s.title + (s.note ? ` · ${s.note}` : "") + " ↗"))))] : null,
      h("div", { class: "eyebrow" }, "备课时用到的"),
      h("ul", { class: "small" }, page.sources.map(s => h("li", {}, h("a", { href: s.url, target: "_blank", rel: "noopener" }, s.title + " ↗")))),
      later.length ? [h("div", { class: "eyebrow" }, "以后再学"), h("ul", { class: "small" }, later.map(l => h("li", {}, l.title)))] : null));
}

// 版本（D-034、D-044）：每一版学到哪、在用哪一版；换版本只换指针，两边的进度都在
function versionFold(u, page, rerender) {
  const vs = page.versions || [];
  const cur = vs.find(v => v.current);
  const day = v => (v.created_at || "").slice(5, 10).replace("-", "/");
  const others = vs.filter(v => !v.current);
  const switchTo = async v => {
    try { await src.switchVersion(u.id, v.plan_id); } catch (e) { return toast("没换成：" + e.message); }
    toast(`换到 ${day(v)} 版了，原来那一版的进度还在`);
    rerender();
  };
  return fold("version", "版本",
    h("span", { class: "sum" }, h("span", {}, cur ? `在用 ${day(cur)} 版` : ""), others.length ? h("b", { style: "color:var(--accent)" }, `另有 ${others.length} 版`) : null),
    h("div", { class: "stack-sm" },
      h("div", { class: "list" }, vs.map(v => h("div", { class: "item", style: "grid-template-columns:5em minmax(0,1fr) auto" },
        h("b", { class: "num" }, day(v)),
        h("div", {}, h("div", {}, v.title), h("div", { class: "xs muted" }, `你学到 ${v.passed}/${v.sections} 节`)),
        v.current ? h("span", { class: "badge badge--ok" }, "在用") : h("button", { class: "btn btn--sm", type: "button", onclick: () => switchTo(v) }, "换到这一版")))),
      others.length ? h("div", { class: "row" }, h("span", { class: "xs muted" }, "改了什么"), mock("D-044：新版改了什么的一句话概括，还没做")) : null));
}

function notReady(u, units) {
  const k = u.state.key;
  let line;
  if (k === "ask") {
    const p = proposalFor(u.id);
    line = h("div", { class: "panel panel--warn row" },
      h("span", { style: "flex:1;min-width:200px" }, "范围还没定，系统不会备它。", p ? p.why : ""),
      h("a", { class: "btn btn--primary", href: `#q.${u.id}` }, "确认范围"));
  } else if (k === "preparing") line = h("p", {}, u.prep.progress?.label || "开始了");
  else if (k === "blocked") line = h("p", {}, u.prep.stage === "interrupted" ? "上次准备中途停了。" : "准备好的内容没通过检验。", h("span", { class: "muted" }, " 可以先学别的。"));
  else line = h("p", { class: "muted" }, u.queued_after ? `学「${units.find(x => x.id === u.queued_after)?.title}」时开始准备。` : "按课程顺序准备。");
  return h("section", { class: "stack-sm" }, line,
    h("dl", { class: "kv" },
      h("dt", {}, "主课里的这一部分"), h("dd", {}, u.sources.length
        ? u.sources.map((s, i) => [i ? "；" : "", s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener" }, s.title + (s.note ? ` · ${s.note}` : "") + " ↗") : s.title])
        : h("span", { class: "muted" }, "—")),
      h("dt", {}, "学科目标"), h("dd", {}, u.subject.goal || "—"),
      h("dt", {}, "我的要求"), h("dd", {}, u.requests.length ? u.requests.join("；") : h("span", { class: "muted" }, "没写"))),
    h("p", { class: "small muted" }, "大纲和先修要求在备好后出现。"));
}

function footer(u) {
  return h("div", { class: "fold row", style: "padding-top:var(--sp-3)" },
    h("a", { href: `#teachers.${u.id}` }, "为什么这样教 · 备课记录 →"),
    h("span", { class: "spacer" }),
    challengeButton({ kind: "unit", id: u.id, label: u.title }, ["这个单元我已经会了", "内容有问题", "想调整重点", "想先学别的"], "对这个单元有异议"));
}
