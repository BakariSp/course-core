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
  prompt/<部件>.md                 system prompt 的部件（D-033）：agent.yaml 的 prompt: 列表写顺序，原样拼接
                                   tutor-prep 现在是 role / principles / learner / tools / workflow / rules
  task.md                          每次运行的任务模板
  evals/rubric.md                  评分标准
  evals/cases.yaml                 评测用例（每个单元"一定要做到的事"）
  evals/runs.jsonl                 每次运行一行：版本组成、用量、全部评分（study.py export 生成，进 git）

data/runs/<agent>/<运行>/          一次运行的工作目录（不进 git）：system.md（拼好的 system prompt）、brief.md、input.json（冻结的输入）、
                                   fetches.jsonl（读过的页面原文）、submissions.jsonl、output.json/.md、raw/（原始日志，可清理）
```

agent 能做什么，完全由环境工具决定。`tutor-prep` 只有两个：

| 工具 | 能做什么 | 限制 |
|---|---|---|
| `fetch_url` | 读网页正文和链接，长页面分段读 | 只能访问 progress/course.yaml 里材料的网站 |
| `submit_plan` | 交课程计划（v2：练习场、检查点、新词、知识节点） | 超预算、一节新词太多、用了没声明的命令、不安全的命令、检查点缺提示或答案、引用了没打开过的页面，都会被退回让它改 |
| `submit_outline` | 交大纲（D-038）：练习场 + 每节的桩（目标、任务、分给这一节的新词、检查点检验什么、依据哪几页） | 超预算、新词超上限、节点不存在、动手型检查点不到一半、依据页面没打开过，都会被退回 |
| `submit_section` | 写一节时交这一节 | 标题、分钟数、目标、任务以大纲为准；新词只能是大纲分给这一节的；接在前面几节后面跑课程计划的全部检查 |
| `submit_repair` | 修复模式（D-035）：只交被指出的部分（地址 → 新内容） | 不在"要重写的部分"里的地址直接拒收；换进去之后，落在这些部分里的问题照样退回 |

## 命令

```bash
python study.py prepare tools-02-git [--from <运行>]              # 备课：大纲 → 各节并行写 → 产出循环（检验 → 定点修复 → 通过就发布，D-035、D-038）
python study.py agent run tutor-prep --unit tools-01-shell       # 只跑一次生成 + 自动评测（调试用，不发布）
python study.py agent eval tutor-prep [--run <运行>]               # 重新评测（默认最近一次；新的评分追加，不覆盖）
python study.py agent review tutor-prep --run <运行> --verdict revise --issue "prompt:给字数上限"   # 开发者抽查（结构化，不是闸门）
python study.py agent report tutor-prep                           # 按版本（Variant）汇总，列出相邻两版差在哪一层
python study.py agent versions tutor-prep                         # 每一版和上一版比改了哪些组成（D-033）
python study.py agent diff tutor-prep --a <版本> --b <版本>          # 两版逐部件的 diff（版本号写前几位）
python study.py lab verify <运行>                                  # 单独把练习场从头走一遍（会在本机执行 agent 写的命令；循环里会自动跑）
python study.py agent publish tutor-prep --run <运行>              # 只有产出循环放行（loop = accepted）的运行能发布
python study.py course-eval tools-01-shell --write                # 学习者用过之后：预测 vs 实际，记为那次运行的学习结果评分
```

需要 `local.env` 里有 `DEEPSEEK_API_KEY`（不进 git），以及 `npm install -g @earendil-works/pi-coding-agent`。

## 版本（Variant，D-022）

一次运行用的"版本"= 岗位说明 + 任务模板 + **上下文配方**（`Harness.build_input` / `render_brief` 的源码）+ 工具（工具实现和计划检查规则的源码）+ 模型 + 运行时（pi 版本和桥接代码），整体一个哈希，
各组成的哈希也分别存下来。改了任何一处就是新版本，`agent report` 会写出和上一版差在哪一层——"一次只改一层"可以直接检查。

## 评测的层次（每一层都是一条 Grade，记着评分器的版本）

产出循环（`studykit/app/prep.py`）每一轮按这个顺序检验，便宜的先跑，前面有阻断就不跑后面的；每个检验器输出**带地址的发现**（D-035）：

1. **自动检查**（确定性，免费）：提交了没有、章节齐不齐、每个链接是否真的打开过、能不能打开、读了几页、用例要求提到的事实，
   以及**命令安全的固定规则**（`specs/cs_practice/safety.py`：sudo、`curl | sh`、删家目录、`git config --global`、练习场外的路径 + 写操作）。
2. **评审模型 · 安全闸门**（`reviewer.md`，和生成模型不同的模型，D-030）：读一遍会被执行的命令。它不放行，后面的实跑就不跑；它出错也算不放行。
3. **练习场实跑**（D-014）：在临时目录里按顺序跑每节的动手命令和参考做法，检查"做之前不通过、做之后通过"。
4. **评审模型 · 质量**（和 2 是同一次调用，按 `evals/rubric.md`）：只有 `block` 级的发现会触发修复，`warn` 只记下。
5. **评分模型**（`agent eval` 手动跑，1–5 分）：只进统计，不当闸门。
6. **开发者抽查**：`agent review` 记结构化结论，喂给改进循环；不是发布的前提。
7. **学习者实际使用**（D-019）：`course-eval` 把每个预测（分钟数、负荷、新词、检查点难度）和学习者用的时候的实际数据对上。
   这是最终的评测：评分模型打 5 分、学习者却卡住的课程，是没做好的课程。

## 迭代纪律

- **先判断问题在哪一层**：prompt、环境工具，还是评测本身。不要把每个问题都变成 prompt 里的一条新规则。
- **一次只改一处**，改完跑全部用例（`evals/cases.yaml`），用 `report` 对比。每次运行都记着 prompt 版本和环境版本，效果变化能归因。
- 评分模型也会错：它的判断要抽查，发现系统性偏差就改评测方法。

## 版本（D-033）

所有调用 LLM 的地方都记版本，而且**每个组成的每一版内容都存在数据库里**（`blob` 表，按内容哈希，只追加），
所以 prompt 文件改掉了、没提交 git，也能取回旧版比 diff。

| 调用点 | 版本的组成 |
|---|---|
| `tutor-prep` | `prompt:<部件>` × N、`task`、`tools`（工具与学科规则的源码）、`context`（上下文配方的源码）、`model`、`runtime` |
| `tutor-prep#judge` | `system`（评分 system prompt）、`rubric`、`model` |
| `short-grader` | `prompt:<部件>` × N、`model` |

改 prompt 的纪律不变：**一次只改一个部件**，跑完看 `agent versions` 里这一版"改了"那一栏是不是只有它。
面板（`/?panel=1`）的"prompt 版本"里能逐版展开 diff。
