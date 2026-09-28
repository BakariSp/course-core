// 待确认（范围没定的单元）和课程设置。
import { h, md, fmtMinutes } from "../lib/dom.js";
import { get, update } from "../lib/store.js";
import { mock, toast } from "../components/ui.js";
import * as src from "../data/source.js";
import { proposalFor } from "../domain/course.js";

// 范围没定是真的（course.yaml 的 scope: open）；拆法提议来自规划老师，还没建（模拟）
export async function questionPage(id) {
  const u = src.flatUnits(await src.course()).find(x => x.id === id);
  if (!u || u.state.key !== "ask") return h("p", {}, "这一项已经不需要确认了。");
  const q = proposalFor(id);
  const head = h("div", { class: "stack-sm" },
    h("a", { class: "small", href: "#course" }, "← 课程"),
    h("div", { class: "row" }, h("span", { class: "badge badge--warn" }, "待确认"), h("span", { class: "small muted" }, u.subject.title)),
    h("h1", {}, `${u.title} · 范围`),
    h("p", { class: "muted" }, "范围还没定，系统不会备它。"));
  if (!q) return h("div", { class: "stack-lg" }, head,
    h("p", {}, mock("规划老师还没建（第三期）"), " 现在要在 progress/course.yaml 里把它拆成几个单元。"));
  const answered = get().answers[q.id];
  const picked = new Set(answered || q.options.filter(o => o.pick).map(o => o.id));
  const total = h("b", { class: "num" });
  const refresh = () => { total.textContent = fmtMinutes(q.options.filter(o => picked.has(o.id)).reduce((a, o) => a + o.minutes, 0)); };
  refresh();
  return h("div", { class: "stack-lg" }, head,
    h("section", { class: "stack-sm" },
      h("div", { class: "row" }, h("span", { class: "eyebrow" }, "系统的提议"), mock("提议来自规划老师（第三期）；你的选择只记在这个浏览器里")),
      h("p", { class: "small muted" }, q.why),
      h("div", { class: "list" }, q.options.map(o => h("label", { class: "item", style: "grid-template-columns:28px minmax(0,1fr) auto;cursor:pointer" },
        h("input", { type: "checkbox", id: `opt-${o.id}`, checked: picked.has(o.id) || undefined, onchange: e => { e.target.checked ? picked.add(o.id) : picked.delete(o.id); refresh(); } }),
        h("div", {}, h("div", { class: "item-title" }, o.title), o.note ? h("div", { class: "item-meta" }, o.note) : null),
        h("span", { class: "small muted num" }, fmtMinutes(o.minutes))))),
      h("div", { class: "sum" }, h("span", {}, "合计 ", total), h("span", {}, "每项一个单元；没勾的放进以后再学"))),
    h("div", { class: "row" },
      h("button", { class: "btn btn--primary", type: "button", onclick: () => {
        update(s => { s.answers[q.id] = [...picked]; });
        toast(`记下了（只在这个浏览器里）：拆成 ${picked.size} 个单元`);
        location.hash = "#course";
      } }, answered ? "改成这样" : "确认")));
}

// 课程设置：阶段、课程清单（都来自 progress/course.yaml）
// D-064：待确认、路线外的课程从首页挪到这里
export async function settingsPage() {
  const c = await src.course();
  const units = src.flatUnits(c);
  const asks = units.filter(u => u.state.key === "ask");
  const others = units.filter(u => !c.path.includes(u.id) && u.state.key !== "ask");
  return h("div", { class: "stack-lg" },
    h("div", { class: "stack-sm" }, h("a", { class: "small", href: "#course" }, "← 课程"), h("h1", {}, "课程设置"),
      c.goal ? h("p", { class: "muted" }, "目标：", c.goal) : null),
    asks.length ? h("section", { class: "stack-sm" },
      h("h2", {}, "待确认"),
      h("p", { class: "small muted" }, "这些单元的范围还没定（一整门讲座、一个频道），系统不会备它们。"),
      h("div", { class: "list" }, asks.map(u => h("div", { class: "item" },
        h("span", { class: "small muted" }, c.path.includes(u.id) ? "在路线上" : "路线外"),
        h("div", {}, h("div", { class: "item-title" }, u.title), h("div", { class: "item-meta" }, u.subject.title)),
        h("a", { class: "btn btn--sm", href: `#q.${u.id}` }, "确认范围"))))) : null,
    others.length ? h("section", { class: "stack-sm" },
      h("h2", {}, "其他课程"),
      h("p", { class: "small muted" }, "不在路线上；想学时在这里打开。"),
      h("div", { class: "list" }, others.map(u => h("div", { class: "item" },
        h("span", { class: "small muted" }, u.subject.title),
        h("a", { class: "item-title", href: `#u.${u.id}` }, u.title),
        h("span"))))) : null,
    h("section", { class: "stack-sm" },
      h("h2", {}, "阶段"),
      h("div", { class: "list" }, c.stages.map(s => h("div", { class: "item", style: "grid-template-columns:6em minmax(0,1fr) auto" },
        h("b", {}, s.title),
        h("div", { class: "small" }, "过关：", s.pass, s.practice ? h("div", { class: "xs muted" }, s.practice) : null),
        h("span", { class: "small muted num" }, `第 ${s.weeks[0]}–${s.weeks[1]} 周`))))),
    h("section", { class: "stack-sm" },
      h("h2", {}, "课程清单"),
      h("div", { class: "table-wrap panel", style: "padding:0" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, ["", "学科", "目标", "主课", "单元"].map(x => h("th", {}, x)))),
        h("tbody", {}, c.subjects.map(s => h("tr", {},
          h("td", {}, s.priority ? h("span", { class: "prio" }, s.priority) : ""),
          h("td", {}, h("b", {}, s.title), h("div", { class: "xs muted" }, (c.stages.find(x => x.id === s.stage) || {}).title || "")),
          md("td", s.goal || "—"),
          h("td", {}, s.sources.map((x, i) => [i ? h("br") : null, h("a", { href: x.url, target: "_blank", rel: "noopener" }, x.title + " ↗")])),
          h("td", { class: "num" }, s.units.length))))))),
    h("section", { class: "stack-sm" },
      h("div", { class: "section-h" }, h("h2", {}, "改课程"), mock("规划老师是第三期；现在改 progress/course.yaml")),
      h("textarea", { id: "want", "aria-label": "改课程", placeholder: "例：先学数据库 / 加一门 Rust / 网络前两章已经会了",
        style: "font:inherit;min-height:80px;padding:8px 10px;border:1px solid var(--border-strong);border-radius:var(--r-sm);background:var(--surface);color:var(--text)" }),
      h("div", {}, h("button", { class: "btn", type: "button", onclick: () => toast("还没接上：规划老师会给出新课程让你确认") }, "提交"))));
}
