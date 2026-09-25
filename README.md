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

### 怎么查文档（Git Bash 没有 `man`）

`man`、`info`、`groff` 都没有，`/usr/share/man` 目录也不存在 —— 因为 Git Bash 只带命令本身，不带手册页。替代方案：

| 想查什么 | Git Bash 里怎么查 |
|---|---|
| bash **内建**命令（`cd` `test` `read` `printf` `trap` `[` `[[` …） | `help <名字>`；`help -m <名字>` 出伪 man 格式；`help -d` 一行简介；`help -s` 只出语法；`help` 列出全部内建命令 |
| **外部**程序（`grep` `ls` `find` `curl` …） | `<程序> --help` |
| **git** 子命令 | `git <子命令> -h`（终端里看，最实用）；`git help <子命令>` 打开浏览器读本地 HTML 文档（`C:\Program Files\Git\mingw64\share\doc\git-doc\`，252 个 `.html`） |
| 真正要 `man` | 装 WSL Ubuntu 后 `sudo apt install man-db manpages manpages-dev`；或查在线 man（注意那是 Linux 版本，选项可能和 Git Bash 里的不同） |

- 坑：`/usr/bin/test --help` **什么都不输出**且 exit=0 —— `test` 把 `--help` 当成一个待测字符串（非空 = 真），根本没进到"打印帮助"的分支。`test` 没有 `--help` 这种入口，只能 `help test`。
- 同类坑：Git Bash 里的程序是 **MinGW/MSYS 版**，选项和 Linux 版常有差异，网上的 man 页不能照抄。

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
knowledge/<学科>.yaml      知识图：知识点 + 先修关系（D-020）—— 你和导师都可以改；状态不写在这里
data/study.db             证据：每一次答题、代码运行、检查点、提示、终端命令、新词反馈、导师观察……（D-021）
                          只追加，数据库触发器拒绝修改和删除；不进 git
progress/evidence.jsonl   证据的可读导出（python study.py export），进 git
progress.md               进度视图（python study.py status 生成）—— 不要手改
```

**一份证据，多个视图（D-020、D-021）**：所有证据用同一个形状（谁 · 做了什么 · 对什么 · 结果），掌握度、薄弱点、进度、已知词表、知识树、学习时长都由它算出来，不手写。
学习时间也是：相邻两个事件间隔不超过 15 分钟就算同一次学习（D-016），`study.py time` 按天列出来。

**掌握度分四级**：1 记忆 → 2 理解 → 3 应用 → 4 分析。每道题都标了等级。某个概念某一级最近 3 次的平均分 ≥ 70%，这一级就算通过；通过后超过 14 天没再练，会被标记为需复习。

## 命令

| 命令 | 作用 |
|---|---|
| `python study.py serve` | 启动答题网页（只监听本机） |
| `python study.py status` | 生成 progress.md |
| `python study.py export` | 把证据和 agent 运行记录导出成 JSONL（进 git） |
| `python study.py log --concept dsa.hash --score 1 --note "LeetCode 1"` | 记录课外练习 |
| `python study.py weak` | 输出学习状态（JSON 格式，给导师看的） |
| `python study.py grade ...` | 导师批改简答题时用 |
| `python study.py runs <课时> [题号]` | 看代码题的作答过程：每次运行的结果和代码改动 |
| `python study.py time` | 每天学了多久、学了什么；`time add --start 2026-09-25T15:30 --minutes 48 --note "看视频"` 补录 |
| `python study.py kg summary` / `kg related <单元>` / `kg show <节点>` | 学习者模型，分三级按需看：全部概况 → 和某个单元相关的 → 一个知识点的全部证据 |
| `python study.py course-eval <单元>` | 课程评测：计划用时、预测负荷、新词预测、检查点难度，各自和实际比 |
| `python study.py lab verify <运行>` | 把 agent 设计的练习场从头走一遍（发布课程前，导师审阅命令后运行） |

## 目录

```
study.py                   命令行入口
studykit/                  分层见 docs/backend-core-design.md §4.2，依赖方向由 tests/test_architecture.py 强制
  domain/                  纯规则，不做 IO：证据信封、掌握度、知识图、课程计划、进度、学习时长、harness 模型
  app/                     用例（learning.py、harness.py）+ 端口（ports.py）
  adapters/                端口的实现：SQLite、YAML 内容文件、pi（agent loop）、抓网页
  specs/cs_practice/       学科 spec「代码学习」：code / terminal 检验器、练习场、课程计划附加规则（D-023）
  web.py · cli.py · agent_tools.py   接口：网页、命令行、agent 的工具入口
  bootstrap.py             组合根：把实现注入用例
data/                      运行时数据（不进 git）：study.db、沙箱、练习场快照（不可重建）、agent 工作目录
web/index.html             答题网页（每种检验器一个渲染函数）
docs/question-format.md    检验器设计和题目格式
templates/lesson/          一套题的模板
lessons/<学科>/<NN-主题>/   题目、答案、代码题、批改记录
scripts/check-shell-env.sh 练习用 shell 的环境自检（tools-01-shell）
tests/                     学习环境自己的测试：python -m pytest tests -q
```
