# cs-study

一个最小的学习环境：**看视频课 → AI 选检验器、出题 → 在网页上实时作答、自动判分 → AI 批改简答题、标记掌握度**。

目标是能验证 AI 写的代码、能独立判断架构、能交付稳定的产品。学什么、按什么顺序学，见 [curriculum.md](curriculum.md)。

## 第一次用

```
python -m pip install -r requirements.txt
python study.py serve
```

打开 http://127.0.0.1:8770/ ，做一遍「演示 · 00」。演示题讲的就是这个环境本身，五种检验器各有一道，不计入掌握度。
（让导师开的话，网页会直接出现在 Claude 桌面应用的浏览器面板里。）

## 一节课的流程

| 步骤 | 你做什么 | 导师做什么 |
|---|---|---|
| 1 | 看完一节视频课，说"我看完了 CS162 第 7 讲 锁" | 在 `syllabus.yaml` 里标记这节课，问你课上讲了哪些要点 |
| 2 | — | 读你的薄弱点，为每个要验证的能力选检验器、出题，打开答题网页 |
| 3 | 在网页上作答：选择和填空当场出结果，代码题可以反复跑测试，终端题在练习终端里敲 git | — |
| 4 | 说"批改" | 给简答题打分，写 `review.md`，告诉你每个概念的掌握度和下次重点 |

## 练习用的 shell（tools-01-shell 起）

shell 这一讲要在真终端里敲命令；网页里的「练习终端」只跑 git（不支持管道、`cd`），所以 shell 的练习在自己的 shell 里做。

| 方案 | 状态 | 适合做什么 |
|---|---|---|
| **Git Bash** | 已装可用（`C:\Program Files\Git\git-bash.exe`） | 现在就用它练 shell 一讲：管道、重定向、变量、`find`/`xargs`、中文都实测通过；缺 tmux、apt、真实权限位 |
| **WSL2 + Ubuntu 24.04** | WSL 2.6.3 已装，但还没有 Linux 发行版；需要管理员跑一次 `wsl --install -d Ubuntu-24.04` | 长期主力：进程、权限、apt、tmux、systemd 那几讲；也是 Docker Desktop 的后端 |
| **Docker** | Docker Desktop 已装，daemon 没启动 | 复现一个服务环境用；不适合当日常 shell（退出即丢状态，还多一层） |

跑一次自检，看这个 shell 能不能拿来练（只读，不改任何文件）：

```bash
bash scripts/check-shell-env.sh
```

- 陷阱：PowerShell 里敲 `bash` 启动的是 WSL（`C:\Windows\system32\bash.exe`），不是 Git Bash。用开始菜单的 **Git Bash**，或在 Windows Terminal（已装）里选 Git Bash profile。
- 路径：Git Bash 里 `/d/cs-study` 就是 `D:\cs-study`；但把 `/d/...` 交给 Windows 程序时，它只认 `D:\...`。
- 本仓库的 shell 脚本必须是 LF 换行（见 [.gitattributes](.gitattributes)）。

## 检验器

| 检验器 | 你看到的 | 怎么判分 |
|---|---|---|
| 选择 | 单选或多选 | 自动；多选选错会倒扣 |
| 填空 | 题干里嵌着输入框，可以有多个空 | 自动，按答对的空给分 |
| 代码 | 带行号的编辑器，可以「展开」成全屏；报错直接指到你代码的第几行；每次运行都有记录 | 自动，按通过的测试比例给分 |
| 终端 | 一个练习终端，跑真实的 git | 自动，检查你做完后仓库的状态 |
| 简答 | 文本框 | 导师按评分点批改 |

所有检验器共用同一个模型（出题 → 交互 → 判分 → 记录），只是界面不同。设计和题目格式见 [docs/question-format.md](docs/question-format.md)。

## 进度数据

```
progress/syllabus.yaml    学了哪些课 —— 你可以直接改（比如标记看完、写笔记）
progress/attempts.jsonl   每一次提交的记录（决定掌握度）—— 只由答题网页和 study.py 追加
progress/runs.jsonl       代码题每次「运行测试」的代码和结果（作答过程）—— 只由答题网页追加
progress.md               由上面两个文件自动生成的视图 —— 不要手改
```

**掌握度分四级**：1 记忆 → 2 理解 → 3 应用 → 4 分析。每道题都标了等级。某个概念某一级最近 3 次的平均分 ≥ 70%，这一级就算通过；通过后超过 14 天没再练，会被标记为需复习。

## 命令

| 命令 | 作用 |
|---|---|
| `python study.py serve` | 启动答题网页（只监听本机） |
| `python study.py status` | 重新生成 progress.md |
| `python study.py log --concept dsa.hash --score 1 --note "LeetCode 1"` | 记录课外练习 |
| `python study.py weak` | 输出学习状态（JSON 格式，给导师看的） |
| `python study.py grade ...` | 导师批改简答题时用 |
| `python study.py runs <课时> [题号]` | 看代码题的作答过程：每次运行的结果和代码改动 |

## 目录

```
study.py                   命令行
studykit/
  store.py                 答题记录、掌握度、progress.md
  lessons.py               读取题目
  checkers/                检验器（每种一个文件）
  server.py                答题网页的后端
web/index.html             答题网页（每种检验器一个渲染函数）
docs/question-format.md    检验器设计和题目格式
templates/lesson/          一套题的模板
lessons/<学科>/<NN-主题>/   题目、答案、代码题、批改记录
scripts/check-shell-env.sh 练习用 shell 的环境自检（tools-01-shell）
tests/                     学习环境自己的测试：python -m pytest tests -q
```
