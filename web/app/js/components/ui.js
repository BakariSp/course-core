// 通用小组件：徽章、模拟标记、步骤条、提示条、分区标题。
import { h } from "../lib/dom.js";

export const badge = st => h("span", { class: `badge badge--${st.tone}${st.busy ? " badge--busy" : ""}` }, st.label);

export const mock = (why = "还没接入系统，数据是示意") => h("span", { class: "mock", title: why }, "模拟");

export function stepper(steps) {
  // steps: [{label, state: "done" | "now" | "fail" | "todo"}]
  return h("ol", { class: "stepper" }, steps.map(s => h("li", { class: s.state },
    h("span", { class: "dot", "aria-hidden": "true" }, s.state === "done" ? "✓" : s.state === "fail" ? "!" : ""),
    s.label)));
}

export function sectionHead(title, ...extra) {
  return h("div", { class: "section-h" }, h("h2", {}, title), ...extra);
}

let toastTimer = null;
export function toast(text) {
  document.querySelector(".toast")?.remove();
  const el = h("div", { class: "toast", role: "status" }, text);
  document.body.append(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 2600);
}

export const meter = (done, total) => h("div", { class: "meter", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(total), "aria-valuenow": String(done) },
  h("i", { style: `width:${Math.round(100 * done / Math.max(1, total))}%` }));
