# 检验器与题目格式

## 设计：一个模型，多种界面

每道题都由一个**检验器（checker）**来出题和判分。所有检验器共用同一套生命周期，区别只在界面：

```
      出题（quiz.yaml + key.yaml）
                │
   view()  ─────┤  网页显示需要的数据（永远不含答案）
                │
   act()   ─────┤  作答过程中的交互：跑测试、执行终端命令……（不记录）
                │
   check() ─────┤  判分 → Verdict（score 0–1 或"待批改"，逐条反馈）
                │
   记录一条 answered 证据 → 掌握度 → progress.md   （这一段与检验器种类无关）
```

| 检验器 | 界面 | 判分 | 适合的等级 |
|---|---|---|---|
| `choice` | 单选（radio）/ 多选（checkbox） | 自动；多选按"选对的减去选错的"给部分分 | 1–2 |
| `fill` | 题干里的每个 `____` 变成一个输入框 | 自动；按答对的空的比例给分 | 1–2 |
| `code` | 网页里的代码编辑器 + 「运行测试」 | 自动：pytest 通过的测试比例 | 3–4 |
| `terminal` | 受限的练习终端，跑真实的 git | 自动：结束时仓库状态满足几条检查 | 3–4 |
| `short` | 文本框 | 单元题：交卷时 LLM 按 rubric 批改（D-031）；其他：导师批改 | 2–4 |

代码：通用的 choice / fill / short 在 `studykit/domain/assessment.py`；要起进程的 code / terminal 属于学科 spec，在 `studykit/specs/cs_practice/`。界面在 `web/index.html` 的 `RENDERERS`。
新增检验器：实现 `Checker` 协议（view / act / check），放进对应学科 spec 的 `checkers`（学什么都成立的放 domain），在 `RENDERERS` 里加一个同名渲染函数，在本文档里补一节，并给它写测试。
检验器不写存储：判分过程中要记下的事（比如每次跑测试）放进返回值的 `records`，由服务层写成证据。

## 整卷模式（单元题，D-031）

`quiz.yaml` 带 `unit:` 的单元题默认 `mode: exam`：

- 交卷前每道题都看不到对错，也没有单题的「提交」；作答草稿存在浏览器里，刷新不丢。
- 代码题可以「运行测试」（看得到测试结果），**每题最多 5 次**，次数由后端记，用完编辑器锁定。改上限：整套题写 `max_runs: N`，或者某道题单独写 `max_runs: N`。
- 页面底部一个「交卷」按钮，后端一次判完整卷：每道题照常记一条 `answered`，另记一条 `submitted_exam` 把它们串起来。
- 简答题由 LLM 批改（`agents/short-grader/`：`grader.yaml` 选模型、列出 prompt 部件，`SYSTEM.md` 是批改员的说明；版本和每一版内容记进数据库，D-033），按 `key.yaml` 的 rubric 逐条给分和理由，记为 actor `short-grader` 的 `graded`，**就是最终分**。每次调用的输入和原始回复在 `data/runs/short-grader/`。
  LLM 失败（模型不可用、回复格式不对）时这道题保持待批改，导师用 `study.py grade` 批。导师也可以对 LLM 批过的题再 `grade` 一次覆盖它（两条都留着，掌握度只算最后一次）。
- rubric 每条末尾写这一条的满分，如 `（0.5）`，各条加起来是 1；不写分值的条目是加分项（总分封顶 1）。

不想整卷交的套题写 `mode: practice`。课程页里的小节检查点不受影响。

## 掌握度等级

| level | 名称 | 能做到 |
|---|---|---|
| 1 | 记忆 | 认得出、说得出定义 |
| 2 | 理解 | 用自己的话解释，预测一段代码或一条命令的结果 |
| 3 | 应用 | 写出能跑对的代码；在终端里完成一个任务 |
| 4 | 分析 | 在陌生代码里找出问题，判断设计取舍 |

某个概念某一级最近 3 次的平均分 ≥ 0.7，这一级就算通过。概念的掌握度 = 已通过的最高一级。

## 概念 id

格式为 `<topic>.<领域>.<概念>`，全部小写英文，例如 `os.sync.race`、`tools.git.branch`。
第一段必须是 `curriculum.md` 里的学科 id。新概念要同时加进 `progress/syllabus.yaml` 对应学科的 `concepts`，并写上中文名。
粒度：一个概念对应"一个能单独答错的点"。太粗（`os.sync`）就看不出薄弱点在哪，太细又不好积累练习次数。

## 文件

```
lessons/<topic>/<NN-slug>/
  quiz.yaml        题目（会发给网页）
  key.yaml         答案、检查项、rubric（只在服务器端读，不会发给网页）
  code/exN.py      代码题的初始代码
  code/test_exN.py 判题测试
  review.md        导师批改记录
```

题干（`prompt`）和选项支持简单的 Markdown：代码块、行内代码、粗体、空行分段。

## 各检验器的写法

每道题都有这几个公共字段：

```yaml
- id: q1                 # 在本套题里唯一
  checker: choice        # 用哪个检验器
  concept: os.sync.race
  level: 1
  kind: bug              # 可选标签：bug（找 bug）| project（nanoteacher 映射）
  prompt: |
    题干
```

### choice

```yaml
# quiz.yaml
  options: [选项 A, 选项 B, 选项 C]
  multi: true            # 可选，默认单选
# key.yaml
  q1: {answer: B, explain: ...}          # 多选：answer: [A, C]
```

### fill

```yaml
# quiz.yaml
  prompt: 两个线程各执行 1000 次 count += 1，count 的最小可能值是 ____，这种问题叫 ____。
# key.yaml
  q2:
    blanks: [["2"], [竞态条件, race condition]]   # 每个空一组可接受的答案；比较时忽略大小写和多余空格
    regex: false                                  # 可选：true 时按正则整串匹配
    explain: ...
```

### code

```yaml
# quiz.yaml
  file: code/ex1.py      # 判题测试默认是同目录的 test_ex1.py，也可以用 test: 另外指定
# key.yaml
  q3: {explain: 参考思路（不要给完整代码）}
```

- 只用标准库和 pytest。
- `exN.py` 只放函数签名、docstring 和 `raise NotImplementedError`。
- 测试在未实现时必须是红的，并且要覆盖边界情况，不能只测最简单的例子。
- 测试文件用 `from exN import ...` 导入。
- 网页里的「运行测试」和「提交」都会把网页里的代码写回 `exN.py`。
- 每次运行和提交都会连同代码快照记成一条 `ran_tests` 证据（作答过程），提交时挂到这次作答上。它不参与掌握度计算，
  导师批改时用 `python study.py runs <课时> [qid]` 看每次的结果和代码改动。
- 测试结果按测试逐条显示；报错如果发生在学习者的代码里，会给出行号并在编辑器里标红。
  写测试时让测试名说清楚在测什么（如 `test_empty_is_zero`），学习者只看得到测试名和报错信息。

### terminal

```yaml
# quiz.yaml
  prompt: 新建 fix-typo 分支，把 app.txt 改成 v2 并提交，然后切回 main。
  intro: 终端里显示的一行提示（可省略）
  setup:                 # 搭场景的命令；学习者看不到输出
    - git init
    - echo v1 > app.txt
    - git add app.txt
    - git commit -m "init"
# key.yaml
  q4:
    checks:              # 分数 = 通过的检查项占比；desc 会作为反馈显示给学习者
      - {run: "git branch --show-current", equals: main, desc: 当前在 main 分支}
      - {run: "git show fix-typo:app.txt", equals: v2, desc: ...}
      - {run: "git log --oneline", line_count: 2, desc: ...}
      - {run: "git log -1 --format=%s", matches: "(?i)fix", desc: ...}
      - {run: "git status --porcelain", equals: "", desc: 工作区是干净的}
      - {file: notes.txt, contains: hello, desc: ...}
      - {file: tmp.txt, absent: true, desc: tmp.txt 已删除}
    explain: 参考做法
```

检查方式：`equals`（去掉首尾空白后完全相等）、`contains`、`not_contains`、`matches`（正则搜索）、`line_count`（非空行数）。

练习终端能做什么：
- 可以用：大部分 git 子命令，以及 `ls`、`cat`、`echo 文本 > 文件`（`>>` 追加）、`touch`、`rm`、`mkdir`、`pwd`、`help`、`clear`。
- 不支持：管道、`cd`、其他程序。
- 被屏蔽的：`git config`、`git -c`、`push/pull/fetch/clone`、`rebase --exec`、`bisect run`、`--output`、`--no-index` 这类能执行任意命令或碰到沙箱外文件的用法。
- 每道题的现场在 `data/sandbox/<lesson>/<qid>/`，用的是独立的 git 配置，不会碰到你自己的 git 设置。

### short

```yaml
# key.yaml
  q5:
    rubric:              # LLM / 导师批改用，不会发给网页；末尾括号是这一条的满分
      - 指出 xxx（0.5）
      - 说出后果 yyy（0.5）
    explain: 批改后才显示
```
