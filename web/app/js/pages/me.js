// 我的：三个标签页，默认「知识点」。
//   知识点   掌握 / 薄弱 / 在学；薄弱一行一条（点开看原因）；按单元
//   学习记录 最近一天 / 近 7 天 / 累计；按天
//   资料     我的资料、时间偏好（只读；在页面上编辑是第二期）；老师的观察（能推翻）
import { h, fmtMinutes } from "../lib/dom.js";
import { mock, toast } from "../components/ui.js";
import { challengeButton } from "../components/challenge.js";
import * as src from "../data/source.js";
import { knowledgeOfUnit, reasonText } from "../domain/course.js";

const TABS = [["know", "知识点"], ["log", "学习记录"], ["profile", "资料"]];

export async function mePage(tab, rerender) {
  tab = TABS.some(([k]) => k === tab) ? tab : "know";
  const units = src.flatUnits(await src.course());
  const titleOf = id => units.find(u => u.id === id)?.title || id;
  const body = tab === "know" ? knowledge(await src.kg(), units, titleOf)
    : tab === "log" ? studyLog(await src.studyLog(), titleOf) : profile(await src.profile(), rerender);
  return h("div", { class: "stack" },
    h("h1", {}, "我的"),
    h("nav", { class: "tabs", "aria-label": "我的" }, TABS.map(([k, label]) =>
      h("a", { href: k === "know" ? "#me" : `#me.${k}`, "aria-current": k === tab ? "page" : undefined }, label))),
    body);
}

const rowSummary = (...kids) => h("summary", { class: "item", style: "grid-template-columns:minmax(0,1fr) auto;cursor:pointer" }, ...kids);

// ---------- 知识点 ----------
function knowledge(graph, units, titleOf) {
  const by = s => graph.nodes.filter(n => n.state === s);
  const weak = by("weak");
  const learned = units.filter(u => u.course);
  return h("div", { class: "stack-lg" },
    h("div", { class: "sum", style: "font-size:var(--fs-md)" },
      h("span", {}, "掌握 ", h("b", {}, by("mastered").length)),
      h("span", { class: "bad" }, "薄弱 ", h("b", {}, weak.length)),
      h("span", {}, "在学 ", h("b", {}, by("learning").length)),
      h("span", {}, "需复习 ", h("b", {}, by("stale").length))),
    weak.length ? h("section", { class: "stack-sm" },
      h("h2", {}, "薄弱"),
      h("div", { class: "list" }, weak.map(n => h("details", { class: "reveal" },
        rowSummary(h("b", {}, n.title), h("span", { class: "small muted" }, (n.units || []).map(titleOf).join("、") || "没有单元教它")),
        h("div", { class: "stack-sm", style: "padding:0 var(--sp-4) var(--sp-3)" },
          h("ul", { class: "small" }, (n.reasons || []).map(r => h("li", {}, reasonText(r)))),
          h("div", {}, challengeButton({ kind: "node", id: n.id, label: n.title }, ["其实我会了", "原因不对"], "其实我会了"))))))) : null,
    h("section", { class: "stack-sm" },
      h("h2", {}, "按单元"),
      h("div", { class: "list" }, learned.map(u => {
        const k = knowledgeOfUnit(graph, u.id);
        return h("details", {},
          rowSummary(h("span", { class: "item-title" }, u.title),
            h("span", { class: "sum" }, k.total ? [h("span", {}, h("b", {}, `${k.mastered}/${k.total}`)), k.weak.length ? h("span", { class: "bad" }, "薄弱 ", h("b", {}, k.weak.length)) : null] : h("span", {}, "—"))),
          h("div", { class: "chips", style: "padding:0 var(--sp-4) var(--sp-3)" }, k.nodes.map(n =>
            h("span", { class: `chip ${n.state === "weak" ? "chip--weak" : n.state === "mastered" ? "chip--ok" : ""}`, title: n.desc || "" }, n.title))));
      })),
      h("p", { class: "xs muted" }, "先修关系图：", h("a", { href: "/?kg=tools" }, "知识树"))));
}

// ---------- 学习记录 ----------
function studyLog(log, titleOf) {
  const sorted = log.days.slice().sort((a, b) => b.date.localeCompare(a.date));
  const latest = sorted[0];
  if (!latest) return h("p", { class: "muted" }, "还没有学习记录。");
  const weekAgo = new Date(new Date(latest.date).getTime() - 6 * 86400000).toISOString().slice(0, 10);
  const week = sorted.filter(d => d.date >= weekAgo).reduce((a, d) => a + d.minutes, 0);
  const label = k => k.replace(/^([a-z]+-\d+(?:-[a-z0-9]+)*)( 第 \d+ 节)?$/, (m, id, sec) => `${titleOf(id)}${sec || ""}`);
  return h("div", { class: "stack-lg" },
    h("div", { class: "sum", style: "font-size:var(--fs-md)" },
      h("span", {}, `${latest.date.slice(5).replace("-", "/")} `, h("b", {}, fmtMinutes(latest.minutes))),
      h("span", {}, "近 7 天 ", h("b", {}, fmtMinutes(week))),
      h("span", {}, "累计 ", h("b", {}, fmtMinutes(log.total_minutes)))),
    h("div", { class: "list" }, sorted.map((d, i) => h("details", { open: i === 0 || undefined },
      rowSummary(h("b", { class: "num" }, d.date), h("span", { class: "small muted num" }, fmtMinutes(d.minutes))),
      h("div", { class: "stack-sm", style: "padding:0 var(--sp-4) var(--sp-3)" }, d.sessions.slice().reverse().map(s => h("div", { class: "small" },
        h("span", { class: "num muted" }, `${s.start.slice(11)}–${s.end.slice(11)}  `),
        Object.entries(s.by_label).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${label(k)} ${fmtMinutes(v)}`).join("、"),
        s.passed?.length ? h("span", { class: "muted" }, ` · 通过 ${s.passed.length} 节`) : null)))))));
}

// ---------- 资料 ----------
function profile(p, rerender) {
  return h("div", { class: "stack-lg" },
    h("section", { class: "stack-sm" },
      h("div", { class: "section-h" }, h("h2", {}, "我的资料"), mock("只读；在页面上编辑是第二期，现在改 progress/profile.yaml")),
      h("div", { class: "list" }, p.about.filter(x => x.value).map(x => h("div", { class: "item", style: "grid-template-columns:7em minmax(0,1fr)" },
        h("div", { class: "small muted" }, x.label), h("div", { class: "small" }, x.value))))),
    h("section", { class: "stack-sm" },
      h("h2", {}, "时间偏好"),
      h("dl", { class: "kv" }, p.time.map(x => [h("dt", {}, x.label), h("dd", {}, x.key === "max_new_terms" ? `${x.value} 个` : fmtMinutes(x.value))]))),
    h("section", { class: "stack-sm" },
      h("h2", {}, "老师的观察"),
      p.observations.length ? h("div", { class: "list" }, p.observations.map(o => observation(o, rerender)))
        : h("p", { class: "small muted" }, "还没有。")));
}

// 推翻一条观察：写一条 refuted_strategy 证据，之后备课、出题的老师不再看到它（D-047）
function observation(o, rerender) {
  const actions = h("div", { class: "row" });
  const ask = () => {
    const note = h("input", { id: `why-${o.id}`, placeholder: "哪里不对（可以不写）", "aria-label": "哪里不对",
      style: "flex:1;min-width:160px;font:inherit;padding:4px 8px;border:1px solid var(--border-strong);border-radius:var(--r-sm);background:var(--surface-2);color:var(--text)" });
    actions.replaceChildren(note,
      h("button", { class: "btn btn--sm btn--primary", type: "button", onclick: async () => {
        try { await src.refuteObservation(o.id, note.value.trim()); } catch (e) { return toast("没记上：" + e.message); }
        toast("记下了：老师以后不再按这条来");
        rerender();
      } }, "确定不对"),
      h("button", { class: "btn btn--sm btn--ghost", type: "button", onclick: () => actions.replaceChildren(open) }, "算了"));
    note.focus();
  };
  const open = h("button", { class: "challenge-btn", type: "button", onclick: ask }, "这条不对");
  actions.append(open);
  return h("div", { class: "item reveal", style: "grid-template-columns:minmax(0,1fr) auto;align-items:start" },
    h("div", {}, h("div", { class: "small" }, o.text), h("div", { class: "xs muted" }, `${o.ts.slice(0, 10)} · ${o.source || o.by}`)),
    actions);
}
