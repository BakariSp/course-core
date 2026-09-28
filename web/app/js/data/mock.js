// 模拟：系统里还没有的部分（D-046 第二、三期）。页面上凡是用到这里的内容，都带「模拟」标记。
// 真实数据在 source.js；这里的东西接上系统之后整块删掉。

// 待确认：缺学习者才有的信息时，老师先给提议（规划老师，第三期）
export const questions = [
  {
    id: "dist-01", unit: "dist-01", title: "Kleppmann 讲座 · 选范围",
    why: "约 8 小时，超过一个单元的上限（3 小时）。按讲座章节拆，已按你的目标勾好建议。",
    options: [
      { id: "d1", title: "引言与系统模型（第 1–2 讲）", minutes: 90, pick: true, note: "后面几讲都用到的基本词" },
      { id: "d2", title: "时间与时钟（第 3 讲）", minutes: 70, pick: true, note: "" },
      { id: "d3", title: "广播与复制（第 4–5 讲）", minutes: 110, pick: true, note: "和你的目标最直接相关" },
      { id: "d4", title: "共识（第 6 讲）", minutes: 80, pick: false, note: "Raft 细节偏深，可以先跳过" },
      { id: "d5", title: "副本一致性（第 7 讲）", minutes: 80, pick: true, note: "" },
      { id: "d6", title: "案例（第 8 讲）", minutes: 60, pick: false, note: "选学" },
    ],
  },
  {
    id: "sysdesign-01", unit: "sysdesign-01", title: "ByteByteGo · 选视频",
    why: "按课程清单「缓存、队列、限流、多租户」挑的，已按你的项目排好顺序。",
    options: [
      { id: "s1", title: "缓存的几种策略与常见坑", minutes: 25, pick: true, note: "" },
      { id: "s2", title: "消息队列：什么时候需要、怎么选", minutes: 25, pick: true, note: "" },
      { id: "s3", title: "限流算法", minutes: 20, pick: true, note: "" },
      { id: "s4", title: "多租户数据隔离的几种做法", minutes: 25, pick: true, note: "" },
      { id: "s5", title: "从 0 扩到 100 万用户", minutes: 30, pick: false, note: "概览" },
    ],
  },
];

// 为什么这样教 · 改变了哪里：需要备课老师在产出里写明依据（第三期）。这里是示意，只有两个单元有
export const influences = {
  "tools-02-git": [
    { because: "已会 cat、grep、管道", effect: "第 3 节读历史直接用 git log | grep，不再解释管道" },
    { because: "你的要求：想学 bisect", effect: "单独一节讲 bisect，放在最后当收尾任务" },
  ],
  "tools-03-debug": [
    { because: "画像：不爱看书，要能马上动手", effect: "整个单元围绕同一个「事故包」推进" },
    { because: "已会 git", effect: "git bisect 放进以后再学，没有重讲" },
  ],
};
