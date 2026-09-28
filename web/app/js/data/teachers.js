// 老师名册：每个调用模型的地方，写给学习者看的说明。agent 对应 agents/<名字>/。
export const teachers = [
  { id: "research", agent: "tutor-prep", stage: "research", name: "调研", does: "读主课讲义，整理成知识点（定义、先修、出处）。和你无关，每个单元只做一次", reads: [] },
  { id: "prep", agent: "tutor-prep", name: "备课", does: "按你的情况写课：先定大纲，各节同时写，拼成一个单元", reads: ["我的资料", "对单元的要求", "已掌握的知识点", "薄弱点", "时间设置"] },
  { id: "review", agent: "tutor-prep#reviewer", name: "评审", does: "检查课程：讲得跳不跳、题目和答案对不对得上、命令安不安全；不通过就指出哪一处，让备课只改那一处", reads: [] },
  { id: "verify", agent: "practice_verify", name: "练习场验证", does: "在临时目录里把每节的练习从头跑一遍，确认照着做能做出来", reads: [] },
  { id: "quiz", agent: "quiz-maker", name: "出题", does: "你打开一个单元的最后一节时，按你在这个单元的表现出单元题", reads: ["这个单元的表现", "薄弱点"] },
  { id: "grade", agent: "short-grader", name: "批改", does: "批改单元题里的简答题：给标准答案，逐条说答对了什么、缺了什么", reads: ["你的回答"] },
  { id: "adjust", agent: null, name: "调整检查", does: "每学完一节，看暴露的问题和后面的单元有没有关系；有就往后面插一段讲解或一道练习", reads: ["这一节的表现"], planned: true },
];
