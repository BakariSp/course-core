# 练习用的 shell（tools-01-shell 起）

网页里的「练习终端」只跑 git：不支持管道、`cd`、其他程序。所以 shell / 操作系统那几讲要在**你自己的终端**里练。
这一页讲：用哪个 shell、怎么自检、踩过的坑、没有 `man` 时怎么查文档。

## 用哪个 shell

| 方案 | 状态 | 适合做什么 |
|---|---|---|
| **Git Bash** | 已装可用（`C:\Program Files\Git\git-bash.exe`） | 现在就用它练 shell：管道、重定向、变量、`find`/`xargs`、中文都实测通过；缺 tmux、apt、真实权限位 |
| **WSL2 + Ubuntu 24.04** | WSL 2.6.3 已装，但还没有 Linux 发行版；需要管理员跑一次 `wsl --install -d Ubuntu-24.04` | 长期主力：进程、权限、apt、tmux、systemd 那几讲；也是 Docker Desktop 的后端 |
| **Docker** | Docker Desktop 已装，daemon 没启动 | 复现一个服务环境用；不适合当日常 shell（退出即丢状态，还多一层） |

跑一次自检，看这个 shell 能不能拿来练（**只读，不改任何文件**；对照输出里哪几项不可用，再决定要不要装 WSL）：

```bash
bash scripts/check-shell-env.sh
```

## 陷阱

- **PowerShell 里的 `bash` 不是 Git Bash**：它启动的是 WSL（`C:\Windows\system32\bash.exe`）。用开始菜单的 **Git Bash**，或在 Windows Terminal（已装）里选 Git Bash profile。
- **路径**：Git Bash 里 `/d/cs-study` 就是 `D:\cs-study`；但把 `/d/...` 交给 Windows 程序时，它只认 `D:\...`。
- **换行**：本仓库的 shell 脚本必须是 LF（见 [.gitattributes](../.gitattributes)）。
- **别在仓库里练手**：练习要在 `data/` 或 `~/shell-lab` 这类地方做。曾经有人在仓库里练 `sed`，把 `lessons/demo/00-env-check/key.yaml` 里的 `answers` 改成了 `answer-editeds`（F-030）。

## 怎么查文档（Git Bash 没有 `man`）

`man`、`info`、`groff` 都没有，`/usr/share/man` 目录也不存在 —— Git Bash 只带命令本身，不带手册页。替代方案：

| 想查什么 | Git Bash 里怎么查 |
|---|---|
| bash **内建**命令（`cd` `test` `read` `printf` `trap` `[` `[[` …） | `help <名字>`；`help -m <名字>` 出伪 man 格式；`help -d` 一行简介；`help -s` 只出语法；`help` 列出全部内建命令 |
| **外部**程序（`grep` `ls` `find` `curl` …） | `<程序> --help` |
| **git** 子命令 | `git <子命令> -h`（终端里看，最实用）；`git help <子命令>` 打开浏览器读本地 HTML 文档（`C:\Program Files\Git\mingw64\share\doc\git-doc\`，252 个 `.html`） |
| 真正要 `man` | 装 WSL Ubuntu 后 `sudo apt install man-db manpages manpages-dev`；或查在线 man（注意那是 Linux 版本，选项可能和 Git Bash 里的不同） |

- 坑：`/usr/bin/test --help` **什么都不输出**且 exit=0 —— `test` 把 `--help` 当成一个待测字符串（非空 = 真），根本没进到"打印帮助"的分支。`test` 没有 `--help` 这种入口，只能 `help test`。
- 同类坑：Git Bash 里的程序是 **MinGW/MSYS 版**，选项和 Linux 版常有差异，网上的 man 页不能照抄。
