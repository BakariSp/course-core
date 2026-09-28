// 路由：地址栏的 #标记 决定显示哪一页。
//   #course（首页）· #u.<单元> · #q.<单元> · #settings · #me[.log|.profile] · #teachers[.<单元>]
// 学习页和答题页还是老页面（/?unit=、/?lesson=）。
import { h, fill } from "./lib/dom.js";
import { subscribe, get } from "./lib/store.js";
import { mock } from "./components/ui.js";
import * as src from "./data/source.js";
import { coursePage } from "./pages/course.js";
import { unitPage } from "./pages/unit.js";
import { mePage } from "./pages/me.js";
import { teachersPage } from "./pages/teachers.js";
import { questionPage, settingsPage } from "./pages/misc.js";

const app = document.getElementById("app");
const navEl = document.getElementById("nav");

const PAGES = {
  course: () => coursePage(),
  settings: () => settingsPage(),
  u: (arg, rerender) => unitPage(arg, rerender),
  q: arg => questionPage(arg),
  me: (arg, rerender) => mePage(arg, rerender),
  teachers: arg => teachersPage(arg),
};
// 顶部导航的高亮：单元、待确认、课程设置都属于「课程」
const SECTION = { course: "course", settings: "course", u: "course", q: "course", me: "me", teachers: "teachers" };

function parse() {
  const raw = decodeURIComponent(location.hash.slice(1)) || "course";
  const [page, ...rest] = raw.split(".");
  return PAGES[page] ? { page, arg: rest.join(".") } : { page: "course", arg: "" };
}

async function renderNav(current) {
  let qs = 0;
  try { qs = src.flatUnits(await src.course()).filter(u => u.state.key === "ask").length; } catch { /* 数字拿不到就不显示 */ }
  const fb = get().feedback.length;
  const link = (key, label, n) => h("a", { href: `#${key}`, "aria-current": current === key ? "page" : undefined }, label,
    n ? h("span", { class: "count", "aria-label": `${n} 项` }, n) : null);
  fill(navEl, link("course", "课程", qs), link("me", "我的"), link("teachers", "老师", fb));
}

let seq = 0, lastKey = "";
async function render({ keepScroll = false } = {}) {
  const { page, arg } = parse();
  const key = `${page}.${arg}`, my = ++seq;
  renderNav(SECTION[page]);                                // 不等它：导航上的数字晚一点出来没关系
  if (key !== lastKey) fill(app, h("p", { class: "muted" }, "加载中…"));
  try {
    const node = await PAGES[page](arg, () => render({ keepScroll: true }));
    if (my !== seq) return;                               // 已经切到别的页了
    const y = window.scrollY;
    fill(app, node);
    window.scrollTo(0, keepScroll || key === lastKey ? y : 0);
  } catch (e) {
    if (my === seq) fill(app, h("p", { class: "error" }, "加载失败：" + e.message));
  }
  lastKey = key;
}

window.addEventListener("hashchange", () => render());
subscribe(() => render({ keepScroll: true }));
// 从学习页回来时数据可能变了（学到第几节、单元题）：离开超过一分钟再回来才刷新。
// WHY: 每次切回来都重画会把展开的折叠块全部收起。
let hiddenAt = 0;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  if (hiddenAt && Date.now() - hiddenAt > 60_000) { src.reset(); render({ keepScroll: true }); }
});
render();

fill(document.getElementById("foot"),
  h("span", {}, "没标 "), mock("示意"), h("span", {}, " 的都是真实数据。新界面在试用（D-046）；"),
  h("a", { href: "/" }, "旧首页"), h("span", {}, " · "), h("a", { href: "/?panel=1" }, "开发者视图"));
