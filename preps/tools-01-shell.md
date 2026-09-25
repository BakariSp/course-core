<!-- 由助教 agent tutor-prep 生成 · prompt 版本 2656383d12 · 模型 deepseek/deepseek-flash · 运行 20260925-153245-tools-01-shell · 评分 4.5/5 -->

# 课前学习指南：Shell（tools-01-shell）

主课：MIT *The Missing Semester of Your CS Education* 2026 版，第 1 讲 **Course Overview + Introduction to the Shell**。

## 这一课学什么

这一讲先说明"为什么还要用文本界面"：shell 不只是启动程序的入口，它自己就是一门编程语言（变量、条件、循环、函数），而且能把一堆小程序用管道拼起来干任意的事。然后从零讲最基础的用法：在文件系统里导航、`$PATH` 怎么找到程序、常用小工具（`cat/sort/uniq/head/tail/grep/sed/find/awk`）、管道与重定向、bash 的 `if`/`for`/`while`、以及怎么写一个 `.sh` 脚本。

对你的目标很直接：nanoteacher 跑在 Linux 服务器上，AI 生成的部署命令、日志排查命令，你得能一步步读懂、能自己在终端里跑一条命令验证结果，而不是靠猜。

## 学习环境

讲义对练习环境有硬要求（原文在 Exercises 开头）：必须用 Unix shell（bash 或 ZSH）；**Windows 上不要用 `cmd.exe` 或 PowerShell**，讲义推荐 Windows Subsystem for Linux 或 Linux 虚拟机。你的 Git Bash 本身就是 bash，可以先在这里练；WSL 里的 Ubuntu 还没装，讲义并没有给安装步骤，装不装都不影响这一讲的练习。

开始前，在 Git Bash 里确认：

1. `echo $SHELL` —— 讲义说输出形如 `/bin/bash` 或 `/usr/bin/zsh` 就说明你在对的程序里；如果什么都没输出，你多半在 cmd/PowerShell 里。
2. 讲义把 `man`（manual）称为入门时最重要的命令，`man ls` 看一个命令的全部参数，`ls --help` 是简化版。Git Bash 里不一定有 `man`，先试 `--help`。
3. 确认练习要用的程序在不在：`which grep sed awk find sort uniq head tail xargs curl`，练习 13 用 `xargs`、练习 14 用 `curl`、练习 15 用 `jq`，后两个缺了就先跳过那两题。
4. 讲义另外推荐装 `tldr`（在终端里直接给常见用法示例）和 `zoxide`（快速 `cd`），都是选做，不是本讲内容。

会遇到的坑：

- 讲义里的路径是 Linux 的：`ls -l /`、`/bin` 在 Git Bash 里看到的是 MSYS 的目录树，不是 `C:\`。练习 1 可以做，但别把目录内容和讲义里的 Linux 机器逐条对照。
- `~` 在 Git Bash 里就是你的 Windows 用户目录，所以练习 2 的 `find ~/Downloads -type f -name "*.zip" -mtime +30` 可以照抄。
- 讲义那段 flaky test 脚本依赖 `stress --cpu 8` 和 `cargo test my_test`，你机器上多半没有。正好练习 11 就是让你把它改成接受参数（`$1` / `$@`）的脚本，直接按这个思路改。
- 练习里出现 `/tmp/mydir`、`/nonexistent`，先 `ls /tmp` 看一眼；不确定就在自己家目录下建练习目录。
- 讲义明说"课上可能有 demo 不在讲义里"，所以视频和讲义不完全同步，以你实际敲出来的结果为准。

## 怎么学

总共约 3–4 小时，建议分两次，每次 1.5–2 小时。

1. **10 分钟**：先扫讲义小标题（Navigating in the shell / What is available in the shell / The shell language），知道自己要学三块东西。
2. **约 1 小时**：看视频。课程介绍页说这门课是九讲、每讲 1 小时；讲义页面里**没有给视频分段或时间戳**，所以按讲义的小节名去找对应片段。
3. **约 1 小时**：回到讲义，每个例子都在 Git Bash 里亲手敲一遍，重点敲管道、重定向、`sed/find/awk` 那几段。
4. **约 1 小时**：做同一页的 Exercises（共 17 题），至少做完 1–13 题。每做完一题用 `echo $?`、`--help` 或换个输入再跑一次来自证结果——这正是你要练的"验证"习惯。
5. 之后去答题网页做题。

## 看哪些部分

- **Who are we / Motivation / Class structure**：选看（5 分钟）。Class structure 值得扫一眼，它说明练习才是这门课的核心。
- **What is the shell? / Why should you care about it?**：必看，很短，讲清文本界面的价值。
- **Navigating in the shell**：必看。`cd`/`pwd`、绝对路径与相对路径、`<TAB>` 补全、`.` 和 `..`，这是你每天登服务器都要用的。
- **What is available in the shell?**：必看，本讲最实用的一块。`$PATH` 与 `which`，以及 `cat`、`sort`、`uniq`、`head`、`tail`、`grep`、`sed -i 's/…/…/g'`、`find`、`awk '{print $2}'`。查日志、检查 AI 改了什么，靠的就是这些。
- **The shell language (bash)**：必看，**和你目标最相关的一段**。pipes（`|`）、重定向（`>` `>>` `<` `tee`）、条件（`if`、`test`/`[ ]`/`[[ ]]`）、循环（`while`、`for v in $(seq 1 10)`）、脚本与 shebang、`set -euo pipefail` 三个开关的含义，以及讲义自己强调的：bash 坑很多，写脚本要用 `shellcheck`。
- **Next steps**：可跳过，只是引出下一讲。
- **Exercises**：必做（至少 1–13 题）。第 4 题（stdin/stdout/stderr 三个流重定向）、第 5 题（`$?`、`&&`、`||`）、第 7–8 题（写脚本 + `chmod +x`）、第 11–12 题（脚本参数、用管道数出现最多的东西）最贴你的需求；第 15 题需要 `jq`。

## 资源

- 讲义正文 Course Overview + Introduction to the Shell — https://missing.csail.mit.edu/2026/course-shell/ — 本单元主材料，先读它。
- 同一页的 Exercises（17 题，讲义未提供答案）— https://missing.csail.mit.edu/2026/course-shell/ — 课后自测。
- 2026 课程总目录 — https://missing.csail.mit.edu/2026/ — 看本讲在九讲中的位置，确认视频录播在 YouTube。
- Command-line Environment（下一讲）— https://missing.csail.mit.edu/2026/command-line-environment/ — 本讲的直接延续：arguments、streams、环境变量、return codes、signals。
- Code Quality — https://missing.csail.mit.edu/2026/code-quality/ — 本讲 `grep`/`sed` 用到的 regular expression 在这里展开，还讲测试、lint、CI（和"验证 AI 代码"最对得上的一讲）。
- 2026 课程视频播放列表（YouTube）— https://www.youtube.com/playlist?list=PLyzOVJj3bHQunmnnTXrNbZnBaCA-ieK4L — 视频入口；我抓取该页时没有拿到具体视频列表，给不出时间戳，请按讲义小节名找片段。

## 学完能做到

1. 能用 `echo $SHELL`、`echo $PATH`、`which` 判断自己在哪个 shell 里、某条命令实际执行的是哪个文件。
2. 能用 `|` 把一个命令的输出交给下一个命令，用 `>` / `>>` 写文件、用 `2>` 单独接 stderr、用 `tee` 一边留完整日志一边只看关心的行。
3. 能用 `find` + `grep`（或 `sed`/`awk`）+ `sort` + `uniq -c` + `head` 拼出一条命令，从一堆文件或日志里数出出现次数最多的东西。
4. 能写一个接受 `$1` 的 `.sh` 脚本，用 `[ -f ... ]` 判断文件是否存在并输出不同信息，`chmod +x` 后运行，并用 `$?` 说明它这次是成功还是失败。
5. 能逐行解释一段脚本里 `#!/bin/bash`、`set -euo pipefail`、`$(date +%s)`、`&`、`$?` 的作用；拿到 AI 写出的一条 shell 命令时，能说出每一步在做什么、结果不对时先查哪里。
