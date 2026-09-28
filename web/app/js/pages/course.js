// 课程（首页，D-064）。学习者的模型：我要上课 → 看一眼上次断到哪 → 接着学。其余都是第二优先级。
//   一级：上次学到哪 + 全页唯一的主按钮「继续学」；下面一行：目标 · 第几阶段 · 学完几课（终点地图收起）
//   二级：课程按阶段分组，当前阶段展开、其余每个一行；单元行不放按钮，点开才有一句说明 + 一个动作
//   别处：待确认、路线外的单元、学科目标与主课 → 课程设置
import { h } from "../lib/dom.js";
import { badge, toast } from "../components/ui.js";
import * as src from "../data/source.js";
import { badgeOf, shortOf, sectionTitle, TAGS, STOPPED } from "../domain/course.js";

export async function coursePage(rerender) {
  const c = await src.course();
  const units = new Map(src.flatUnits(c).map(u => [u.id, u]));
  return h("div", { class: "stack-lg" }, hero(c, units), phases(c, units, rerender));
}

// ---------- 上次学到哪 ----------

function hero(c, units) {
  const n = c.next[0];
  const onPath = c.path.map(id => units.get(id));
  const done = onPath.filter(u => u.state.key === "done").length;
  const at = c.phases.findIndex(p => p.current);
  const where = h("div", { class: "small muted" },
    c.goal ? `目标：${c.goal}` : "",
    c.phases.length ? ` · ${at >= 0 ? `第 ${at + 1} 阶段 / 共 ${c.phases.length} 个` : "路线学完了"} · ${done}/${onPath.length} 课学完` : "");
  if (!n) return h("section", { class: "panel panel--hero stack-sm" },
    h("h1", {}, "接下来的课还在备"), h("p", { class: "muted" }, "备好了会出现在这里。"), where, destination(c));
  const s = n.section;
  const resume = n.kind === "continue" || Boolean(units.get(n.unit)?.last_at);    // 开过这一课就是"接着学"，不是"开始"
  return h("section", { class: "panel panel--hero stack-sm", "aria-labelledby": "continue-h" },
    h("div", { class: "eyebrow", id: "continue-h" }, resume ? "上次学到" : "下一课"),
    h("div", { class: "continue" },
      h("div", {}, h("h1", {}, n.title),
        s ? h("div", { class: "muted" }, `第 ${s.index + 1} 节 · ${sectionTitle(s)}${s.minutes ? ` · ${s.minutes} 分钟` : ""}`) : null),
      h("a", { class: "btn btn--primary", href: src.learnHref(n.unit) }, resume ? "继续学" : "开始学")),
    where, destination(c));
}

// 终点地图（D-062）：收起，只在想看"学这些是为了什么"时打开
function destination(c) {
  if (!c.destination?.length) return null;
  return h("details", { class: "small" },
    h("summary", { class: "muted", style: "cursor:pointer" }, `全链路地图 · ${c.destination.length} 段`),
    h("dl", { class: "kv", style: "grid-template-columns:3em minmax(0,1fr);margin-top:6px" }, c.destination.map(x => [
      h("dt", {}, h("span", { class: "tag tag--accent" }, x.id)),
      h("dd", {}, h("div", {}, x.can), h("div", { class: "xs muted" }, "验收：", x.accept))])));
}

// ---------- 课程：按阶段 ----------

function phases(c, units, rerender) {
  const capName = id => (c.destination.find(x => x.id === id)?.can || id).split("：")[0];
  const groups = c.phases.length ? c.phases : [{ title: "全部课程", fills: [], units: [...units.keys()], current: true }];
  return h("section", { class: "stack-sm", "aria-labelledby": "catalog-h" },
    h("div", { class: "section-h" }, h("h2", { id: "catalog-h" }, "课程"), h("span", { class: "spacer" }),
      h("a", { class: "small", href: "#settings" }, "课程设置")),
    h("div", {}, groups.map((p, i) => {
      const us = p.units.map(id => units.get(id));
      const done = us.filter(u => u.state.key === "done").length;
      return h("details", { class: "group", open: p.current || undefined },
        h("summary", {}, h("div", { class: "group-title" },
          h("span", { class: "prio" }, done === us.length ? "✓" : String(i + 1)),
          h("h3", {}, p.title),
          p.fills.length ? h("span", { class: "small muted" }, "补 " + p.fills.map(capName).join("、")) : null,
          h("span", { class: "small muted num" }, `${done}/${us.length} 课学完`))),
        h("div", { class: "group-body" }, h("div", { class: "list" }, us.map(u => unitRow(u, units, rerender)))));
    })));
}

function unitRow(u, units, rerender) {
  const facts = shortOf(u);
  return h("details", {},
    h("summary", { class: "item", style: "cursor:pointer" },
      badge(badgeOf(u)),
      h("div", { class: "row", style: "gap:6px" },
        h("span", { class: "item-title" }, u.title),
        facts ? h("span", { class: "small muted num" }, facts) : null,
        ...u.state.tags.map(t => h("span", { class: "tag tag--accent" }, TAGS[t] || t))),
      h("span", { class: "small muted", "aria-hidden": "true" }, "▾")),
    h("div", { class: "row", style: "padding:var(--sp-2) var(--sp-4) var(--sp-3);gap:var(--sp-3)" },
      h("span", { class: "small", style: "flex:1;min-width:200px" }, explain(u, units)),
      unitAction(u, rerender),
      h("a", { class: "small", href: `#u.${u.id}` }, "单元详情 →")));
}

// 展开后的一句说明：发生了什么、接下来会怎样
function explain(u, units) {
  const k = u.state.key, c = u.course, q = u.quiz;
  if (k === "done") return ["各节都走过了。", c.skipped ? `跳过了 ${c.skipped} 节，里面的题会在复习时再出现。` : "",
    q ? `单元题做了 ${q.answered}/${q.questions} 道。` : ""].join("");
  if (k === "learning") return `走过 ${c.walked}/${c.sections} 节。`;
  if (k === "ready") return `已经备好，一共 ${c.sections} 节。`;
  if (k === "preparing") return "正在备，备好了这里会自己变成「备好了」。";
  if (k === "blocked") return u.prep.stage === "interrupted" ? "上次备课中途停了，服务下次启动时会自动接着备。"
    : `备课卡住了：${STOPPED[u.prep.stopped] || STOPPED.budget}，要开发者看。可以先学别的。`;     // 不给学习者按钮（D-063）
  if (k === "ask") return "范围还没定，系统不会备它。";
  const prev = u.queued_after && units.get(u.queued_after);
  return prev ? `学「${prev.title}」时会自动备好。想跳过去现在学，可以现在就备。` : "按路线顺序自动备。";
}

// 一个动作。跳课是学习者的决定：先说清花费再开始（D-064）
export function unitAction(u, rerender) {
  const k = u.state.key;
  if (k === "learning") return h("a", { class: "btn btn--sm", href: src.learnHref(u.id) }, "继续学");
  if (k === "ready") return h("a", { class: "btn btn--sm", href: src.learnHref(u.id) }, "开始学");
  if (k === "done") return h("a", { class: "btn btn--sm", href: src.learnHref(u.id) }, "复习");
  if (k === "ask") return h("a", { class: "btn btn--sm", href: `#q.${u.id}` }, "确认范围");
  if (k === "queued") {                                   // 跳课是学习者的决定；卡住的交给系统和开发者（D-063）
    const label = "现在备这一课";
    return h("button", { class: "btn btn--sm", type: "button", onclick: async e => {
      if (!confirm(`${label}：AI 写讲解和练习，再检查一遍，最多 3 轮、大约 $1。点「确定」开始。`)) return;
      e.target.disabled = true;
      try { await src.prepare(u.id); toast("开始备了，备好会显示「备好了」"); rerender?.(); }
      catch (err) { e.target.disabled = false; toast("没能开始：" + err.message); }
    } }, label);
  }
  return h("span");
}
