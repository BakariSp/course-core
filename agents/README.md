# 助教 agent

课前讲解、找资料这类工作交给 agent 做；导师（Claude Code）不直接写内容，只**改 agent 的 prompt 和评测它的产出**。

## 分层

```
pi（agent 引擎，npm 全局安装，不改它的源码）
 └─ 由 runner 以最小权限启动：不读任何用户配置 / 上下文文件 / 技能，关闭全部内置工具
     └─ agents/_pi/env_bridge.ts   薄适配器：把环境工具注册给 pi，调用时转发给 Python
         └─ studykit/agent_env/    学习环境：工具、简报、运行记录、评测（Python，有测试）

agents/<agent>/                    一个 agent = 一个文件夹，全是配置
  agent.yaml                       模型、开放哪些工具、输出要求、评测设置
  SYSTEM.md                        岗位说明（prompt）
  task.md                          每次运行的任务模板
  evals/rubric.md                  评分标准
  evals/cases.yaml                 评测用例（每个单元"一定要做到的事"）
  evals/results.jsonl              每次评测一行摘要（进 git，用来比较版本）
```

agent 能做什么，完全由环境工具决定。`tutor-prep` 只有两个：

| 工具 | 能做什么 | 限制 |
|---|---|---|
| `fetch_url` | 读网页正文和链接，长页面分段读 | 只能访问 curriculum.md 里出现过的网站 |
| `submit_plan` | 交课程计划（v2：练习场、检查点、新词、知识节点） | 超预算、一节新词太多、用了没声明的命令、检查点缺提示或答案、引用了没打开过的页面，都会被退回让它改 |

## 命令

```bash
python study.py agent run tutor-prep --unit tools-01-shell       # 运行 + 自动评测
python study.py agent eval tutor-prep [--run <运行目录名>]         # 重新评测（默认最近一次）
python study.py agent report tutor-prep                           # 按 prompt 版本 / 环境版本汇总
python study.py lab verify <运行目录名>                            # 导师读过命令后：把练习场从头走一遍（会在本机执行 agent 写的命令）
python study.py agent publish tutor-prep --run <运行目录名>        # 自动检查全通过、练习场验证通过才能发布到 preps/
python study.py course-eval tools-01-shell --write                # 学习者用过之后：预测 vs 实际，写进运行目录
```

需要 `local.env` 里有 `DEEPSEEK_API_KEY`（不进 git），以及 `npm install -g @earendil-works/pi-coding-agent`。

## 评测的三层

1. **自动检查**（确定性，免费）：提交了没有、章节齐不齐、每个链接是否真的打开过、能不能打开、读了几页、长度、用例要求提到的事实。
2. **评分模型**（更强的模型，按 rubric 打 1–5 分）：它看不到页面正文，所以怀疑"编造"时要列出原文引用，由程序到 agent 读过的页面里核对。
3. **导师审阅**：写在运行目录的 `review.md`，决定发不发布、下一次改什么。
4. **练习场验证**（D-014）：`lab verify` 按顺序跑每节的动手命令和参考做法，检查"做之前不通过、做之后通过"。
   它会执行 agent 写的命令，所以不在 agent 提交时自动跑，而是导师读过之后手动跑。
5. **学习者实际使用**（D-019）：`course-eval` 把每个预测（分钟数、负荷、新词、检查点难度）和学习者用的时候的实际数据对上。
   这是最终的评测：评分模型打 5 分、学习者却卡住的课程，是没做好的课程。

## 迭代纪律

- **先判断问题在哪一层**：prompt、环境工具，还是评测本身。不要把每个问题都变成 prompt 里的一条新规则。
- **一次只改一处**，改完跑全部用例（`evals/cases.yaml`），用 `report` 对比。每次运行都记着 prompt 版本和环境版本，效果变化能归因。
- 评分模型也会错：它的判断要抽查，发现系统性偏差就改评测方法。
