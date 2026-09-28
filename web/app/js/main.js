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

// 重画时保住展开的折叠块：记下展开的 <details> 的标题，画完再按标题打开。
const openFolds = () => new Set([...app.querySelectorAll("details[open] > summary")].map(s => s.textContent));
const reopen = keep => app.querySelectorAll("details > summary").forEach(s => { if (keep.has(s.textContent)) s.parentElement.open = true; });

// 有单元在准备中（带忙碌标记的徽章或步骤条）：每 5 秒重新拿一次数据，备好了状态自己变（D-063）。页面在后台时不拿。
let pollTimer = null;
function pollWhileBusy(key) {
  clearTimeout(pollTimer);
  if (!app.querySelector(".badge--busy, .stepper li.now")) return;
  pollTimer = setTimeout(() => {
    const { page, arg } = parse();
    if (`${page}.${arg}` !== key) return;
    if (document.hidden) return pollWhileBusy(key);
    src.reset();
    render({ keepScroll: true });
  }, 5000);
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
    const y = window.scrollY, folds = key === lastKey ? openFolds() : new Set();
    fill(app, node);
    reopen(folds);
    window.scrollTo(0, keepScroll || key === lastKey ? y : 0);
    pollWhileBusy(key);
  } catch (e) {
    if (my === seq) fill(app, h("p", { class: "error" }, "加载失败：" + e.message));
  }
  lastKey = key;
}

window.addEventListener("hashchange", () => render());
subscribe(() => render({ keepScroll: true }));
// 从学习页回来时数据可能变了（学到第几节、单元题）：离开超过一分钟再回来才刷新。
// WHY: 不是每次切回来都重画：会丢掉页面上正在看的位置。
let hiddenAt = 0;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  if (hiddenAt && Date.now() - hiddenAt > 60_000) { src.reset(); render({ keepScroll: true }); }
});
render();

fill(document.getElementById("foot"),
  h("span", {}, "没标 "), mock("示意"), h("span", {}, " 的都是真实数据。新界面在试用（D-046）；"),
  h("a", { href: "/" }, "旧首页"), h("span", {}, " · "), h("a", { href: "/?panel=1" }, "开发者视图"));
