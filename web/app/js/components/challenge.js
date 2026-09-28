// 反馈：「这里不对」。出现在它针对的东西旁边（小节、单元、知识点、老师做的事）。
// 模拟：现在只记在这个浏览器里，在「老师 · 我的反馈」能看到；接上系统后是一条证据，由老师处理（D-046 第三期）。
import { h } from "../lib/dom.js";
import { update } from "../lib/store.js";
import { mock, toast } from "./ui.js";

// target: { kind, id, label }；kinds: 这个对象上常见的几种说法
export function challengeButton(target, kinds, text = "这里不对") {
  return h("button", { class: "challenge-btn", type: "button", onclick: () => openSheet(target, kinds) }, text);
}

function openSheet(target, kinds) {
  let picked = kinds[0];
  const note = h("textarea", { id: "feedback-note", placeholder: "补充（可以不写）", "aria-label": "补充说明" });
  const buttons = kinds.map(k => h("button", { type: "button", "aria-pressed": String(k === picked), onclick: e => {
    picked = k;
    buttons.forEach(b => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
  } }, k));
  const close = () => mask.remove();
  const submit = () => {
    update(s => s.feedback.unshift({ at: new Date().toISOString(), target, kind: picked, note: note.value.trim() }));
    close();
    toast("已记下，在「老师 · 我的反馈」里");
  };
  const mask = h("div", { class: "sheet-mask", onclick: e => { if (e.target === mask) close(); } },
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": "反馈" },
      h("div", { class: "row" }, h("span", { class: "eyebrow" }, "反馈"), mock("只记在这个浏览器里；老师处理反馈是第三期")),
      h("h3", {}, target.label),
      h("div", { class: "choice" }, buttons),
      note,
      h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", { class: "btn btn--ghost", type: "button", onclick: close }, "算了"),
        h("button", { class: "btn btn--primary", type: "button", onclick: submit }, "提交"))));
  document.body.append(mask);
  mask.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  buttons[0]?.focus();
}
