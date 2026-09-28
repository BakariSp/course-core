// 老师。
//   一级：我的反馈（有的时候）；动态（最近 5 条）
//   二级：老师（一行一位，点开看读我的哪些信息）
//   收起：费用
//   按单元（从单元页进来）：为什么这样教 · 备课记录 · 这个单元的动态
import { h } from "../lib/dom.js";
import { get } from "../lib/store.js";
import { mock, stepper } from "../components/ui.js";
import { challengeButton } from "../components/challenge.js";
import * as src from "../data/source.js";
import { teachers } from "../data/teachers.js";
import { influences } from "../data/mock.js";
import { describeRun, prepSteps, STOPPED } from "../domain/course.js";

export async function teachersPage(unitId) {
  const units = src.flatUnits(await src.course());
  const u = unitId && units.find(x => x.id === unitId);
  // 动态：有备课记录的单元，每次运行一条（出题、批改的运行还没有接口）
  const withRuns = u ? [u] : units.filter(x => x.prep.stage !== "todo" || x.course);
  const details = await Promise.all(withRuns.map(x => src.prepRuns(x.id).then(d => ({ unit: x, runs: d.runs }))));
  const events = details.flatMap(d => d.runs.map(r => ({ unit: d.unit, run: r, ...describeRun(r) })))
    .sort((a, b) => b.run.started.localeCompare(a.run.started));
  return h("div", { class: "stack-lg" },
    h("h1", {}, "老师"),
    u ? await unitView(u, details[0]?.runs || []) : myFeedback(),
    activity(events, !!u),
    u ? h("a", { class: "small", href: "#teachers" }, "全部老师和动态 →") : roster(events),
    u ? null : spend(events));
}

// ---------- 按单元：为什么这样教 · 备课记录 ----------
async function unitView(u, runs) {
  const ctx = await src.prepInput(u.id).catch(() => null);
  const block = key => ctx?.blocks.find(b => b.key === key)?.value;
  const known = block("known_titles") || [], related = block("related") || {};
  const inf = influences[u.id] || [];
  const p = u.prep;
  const S = (label, state) => ({ label, state });
  const done = p.stage === "published" || p.stage === "ready";
  const steps = p.stage === "preparing" ? prepSteps(p.progress)
    : done || u.course ? [S("整理讲义", "done"), S("大纲与各节", "done"), S("检验", "done"), S("发布", "done")]
    : p.stage === "todo" ? [S("整理讲义", u.knowledge ? "done" : "todo"), S("大纲与各节", "todo"), S("检验", "todo"), S("发布", "todo")]
    : [S("整理讲义", u.knowledge ? "done" : "todo"), S("大纲与各节", "fail"), S("检验", "todo"), S("发布", "todo")];
  return h("section", { class: "panel stack" },
    h("a", { class: "item-title", href: `#u.${u.id}` }, `← ${u.title}`),
    h("div", { class: "stack-sm" },
      h("h2", {}, "为什么这样教"),
      h("dl", { class: "kv" },
        h("dt", {}, "读了"), h("dd", { class: "small" }, `已掌握的 ${known.length} 个知识点`,
          related.weak?.length ? `、相关的薄弱点 ${related.weak.length} 个（${related.weak.map(w => w.title).join("、")}）` : "",
          u.requests.length ? `、你的要求（${u.requests.join("；").slice(0, 40)}）` : "", "、我的资料、老师的观察",
          " ", mock("这是现在的输入；发布那一版当时读的是什么，要后端开接口")),
        h("dt", {}, "改变了哪里"), h("dd", {}, inf.length
          ? h("div", { class: "stack-sm", style: "gap:4px" }, mock("示意：要备课老师在产出里写明依据（第三期）"),
              inf.map((x, i) => h("div", { class: "small row reveal" }, h("span", { class: "muted" }, x.because), "→", h("span", {}, x.effect),
                challengeButton({ kind: "influence", id: `${u.id}#${i}`, label: x.effect }, ["没必要", "不够", "理由不对"]))))
          : mock("要备课老师在产出里写明依据（第三期）")))),
    h("div", { class: "stack-sm" },
      h("h2", {}, "备课记录"),
      stepper(steps),
      runs.length ? h("div", { class: "small muted" }, `一共 ${runs.length} 次运行 · ${runs.reduce((a, r) => a + r.minutes, 0).toFixed(0)} 分钟 · $${runs.reduce((a, r) => a + r.cost_usd, 0).toFixed(2)}`) : null,
      ["interrupted", "escalated"].includes(p.stage) ? h("div", { class: "small" },
        p.stage === "interrupted" ? "上次准备中途停了，服务下次启动时会自动接着备。"
          : `卡住了：${STOPPED[p.stopped] || STOPPED.budget}。卡在：${p.stuck?.[0] || ""}`) : null));
}

// ---------- 我的反馈（模拟：只在这个浏览器里） ----------
function myFeedback() {
  const list = get().feedback;
  if (!list.length) return null;
  const KIND = { unit: "单元", section: "小节", node: "知识点", influence: "为什么这样教", feed: "动态" };
  return h("section", { class: "stack-sm" },
    h("div", { class: "section-h" }, h("h2", {}, "我的反馈"), mock("只记在这个浏览器里；老师处理反馈是第三期")),
    h("div", { class: "list" }, list.map(c => h("div", { class: "item", style: "grid-template-columns:minmax(0,1fr) auto" },
      h("div", {}, h("b", {}, c.kind), h("span", { class: "muted" }, ` · ${KIND[c.target.kind] || ""} · ${c.target.label}`),
        c.note ? h("div", { class: "small" }, c.note) : null),
      h("span", { class: "badge badge--wait" }, "已记下")))));
}

// ---------- 动态 ----------
function activity(events, forUnit) {
  const show = events.slice(0, 5), rest = events.slice(5, 60);           // 一级只放最近 5 条，其余收起（D-046 分层）
  const when = s => s.slice(5, 16).replace("T", " ").replace("-", "/");
  const row = e => h("li", { class: "reveal" },
    h("time", {}, when(e.run.started)),
    h("div", { class: "row", style: "align-items:flex-start;gap:6px" },
      h("span", { style: "flex:1;min-width:200px" }, h("b", {}, e.teacher), " · ",
        forUnit ? null : [h("a", { href: `#u.${e.unit.id}` }, e.unit.title), " · "],
        e.what, e.verdict ? ` · ${e.verdict}` : "", e.published ? " · 你现在学的就是这一版" : ""),
      challengeButton({ kind: "feed", id: e.run.id, label: `${e.unit.title} · ${e.what}` }, ["没必要", "做错了", "看不懂"])));
  return h("section", { class: "stack-sm" },
    h("div", { class: "section-h" }, h("h2", {}, "动态"), forUnit ? null : h("span", { class: "xs muted" }, "只有备课")),
    show.length ? h("ol", { class: "timeline panel", style: "padding-block:var(--sp-1)" }, show.map(row)) : h("p", { class: "small muted" }, "还没有。"),
    rest.length ? h("details", {}, h("summary", { class: "small", style: "cursor:pointer" }, `更早的 ${rest.length} 条`),
      h("ol", { class: "timeline" }, rest.map(row))) : null);
}

// ---------- 老师 ----------
function roster(events) {
  const last = name => events.find(e => e.teacher === name);
  return h("section", { class: "stack-sm" },
    h("h2", {}, "老师"),
    h("div", { class: "list" }, teachers.map(t => {
      const l = last(t.name);
      return h("details", {},
        h("summary", { class: "item", style: "grid-template-columns:6.5em minmax(0,1fr) auto;cursor:pointer" },
          h("b", {}, t.name), h("span", { class: "small" }, t.does.split("：")[0].split("。")[0]), t.planned ? h("span", { class: "tag" }, "还没上线") : h("span")),
        h("dl", { class: "kv small", style: "padding:0 var(--sp-4) var(--sp-3)" },
          h("dt", {}, "做什么"), h("dd", {}, t.does),
          h("dt", {}, "读我的"), h("dd", {}, t.reads.length ? h("span", { class: "used-by" }, t.reads.map(r => h("span", {}, r))) : h("span", { class: "muted" }, "不读")),
          l ? [h("dt", {}, "最近一次"), h("dd", {}, `${l.run.started.slice(5, 16).replace("T", " ")} · ${l.unit.title} · ${l.what}`)] : null));
    })));
}

// ---------- 费用 ----------
function spend(events) {
  const total = events.reduce((a, e) => a + e.run.cost_usd, 0);
  const cutoff = new Date(Date.now() - 7 * 86400000).toISOString();
  const week = events.filter(e => e.run.started >= cutoff).reduce((a, e) => a + e.run.cost_usd, 0);
  return h("details", { class: "fold" },
    h("summary", {}, h("h2", {}, "费用"), h("span", { class: "sum" }, h("span", {}, "近 7 天 ", h("b", {}, `$${week.toFixed(2)}`)), h("span", {}, "累计 ", h("b", {}, `$${total.toFixed(2)}`)))),
    h("div", { class: "fold-body small muted" }, "只算备课运行本身。", mock("评审、出题、批改的费用都记了账（D-045），还没有汇总接口")));
}
