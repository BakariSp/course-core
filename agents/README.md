# 助教 agent

课前讲解、找资料这类工作交给 agent 做；导师（Claude Code）不直接写内容，只**改 agent 的 prompt 和评测它的产出**。

## 分层

```
pi（agent loop，npm 全局安装，不改它的源码；借来的，可以换——studykit/adapters/pi.py 是唯一认识它的地方）
 └─ 以最小权限启动：不读任何用户配置 / 上下文文件 / 技能，关闭全部内置工具
     └─ agents/_pi/env_bridge.ts   薄适配器：把环境工具注册给 pi，调用时转发给 python -m studykit.agent_tools
         └─ studykit/app/harness.py  harness（D-022）：上下文配方、工具、运行记录、评测、发布闸门、版本对比

agents/<agent>/                    一个 agent = 一个文件夹，全是配置
  agent.yaml                       模型、开放哪些工具、输出要求、评测设置
  SYSTEM.md                        岗位说明（prompt）
  task.md                          每次运行的任务模板
  evals/rubric.md                  评分标准
  evals/cases.yaml                 评测用例（每个单元"一定要做到的事"）
  evals/runs.jsonl                 每次运行一行：版本组成、用量、全部评分（study.py export 生成，进 git）

data/runs/<agent>/<运行>/          一次运行的工作目录（不进 git）：brief.md、input.json（冻结的输入）、
                                   fetches.jsonl（读过的页面原文）、submissions.jsonl、output.json/.md、raw/（原始日志，可清理）
```

agent 能做什么，完全由环境工具决定。`tutor-prep` 只有两个：

| 工具 | 能做什么 | 限制 |
|---|---|---|
| `fetch_url` | 读网页正文和链接，长页面分段读 | 只能访问 curriculum.md 里出现过的网站 |
| `submit_plan` | 交课程计划（v2：练习场、检查点、新词、知识节点） | 超预算、一节新词太多、用了没声明的命令、检查点缺提示或答案、引用了没打开过的页面，都会被退回让它改 |

## 命令

```bash
python study.py agent run tutor-prep --unit tools-01-shell       # 运行 + 自动评测
python study.py agent eval tutor-prep [--run <运行>]               # 重新评测（默认最近一次；新的评分追加，不覆盖）
python study.py agent review tutor-prep --run <运行> --verdict revise --issue "prompt:给字数上限"   # 导师审阅（结构化）
python study.py agent report tutor-prep                           # 按版本（Variant）汇总，列出相邻两版差在哪一层
python study.py lab verify <运行>                                  # 导师读过命令后：把练习场从头走一遍（会在本机执行 agent 写的命令）
python study.py agent publish tutor-prep --run <运行>              # 自动检查全通过、练习场验证通过才能发布
python study.py course-eval tools-01-shell --write                # 学习者用过之后：预测 vs 实际，记为那次运行的学习结果评分
```

需要 `local.env` 里有 `DEEPSEEK_API_KEY`（不进 git），以及 `npm install -g @earendil-works/pi-coding-agent`。

## 版本（Variant，D-022）

一次运行用的"版本"= 岗位说明 + 任务模板 + **上下文配方**（`Harness.build_input` / `render_brief` 的源码）+ 工具（工具实现和计划检查规则的源码）+ 模型 + 运行时（pi 版本和桥接代码），整体一个哈希，
各组成的哈希也分别存下来。改了任何一处就是新版本，`agent report` 会写出和上一版差在哪一层——"一次只改一层"可以直接检查。

## 评测的层次（每一层都是一条 Grade，记着评分器的版本）

1. **自动检查**（确定性，免费）：提交了没有、章节齐不齐、每个链接是否真的打开过、能不能打开、读了几页、长度、用例要求提到的事实。
2. **评分模型**（更强的模型，按 rubric 打 1–5 分）：它看不到页面正文，所以怀疑"编造"时要列出原文引用，由程序到 agent 读过的页面里核对。
3. **导师审阅**：`agent review`，结论（publish / revise / reject）+ 每个问题标在哪一层，喂回下一次迭代。
4. **练习场验证**（D-014）：`lab verify` 按顺序跑每节的动手命令和参考做法，检查"做之前不通过、做之后通过"。
   它会执行 agent 写的命令，所以不在 agent 提交时自动跑，而是导师读过之后手动跑。
5. **学习者实际使用**（D-019）：`course-eval` 把每个预测（分钟数、负荷、新词、检查点难度）和学习者用的时候的实际数据对上。
   这是最终的评测：评分模型打 5 分、学习者却卡住的课程，是没做好的课程。

## 迭代纪律

- **先判断问题在哪一层**：prompt、环境工具，还是评测本身。不要把每个问题都变成 prompt 里的一条新规则。
- **一次只改一处**，改完跑全部用例（`evals/cases.yaml`），用 `report` 对比。每次运行都记着 prompt 版本和环境版本，效果变化能归因。
- 评分模型也会错：它的判断要抽查，发现系统性偏差就改评测方法。
