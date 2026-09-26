# 终端命令问答记录

学习者提问 + 简短解答。只做记录，不改任何代码或仓库文件。

（按提问顺序追加，最新的放最下面。）

---

## Q1. `ls */README.md; git log --oneline -3 -- README.md` 每个部分是什么意思？

拆成两部分看：前半句看**工作区现在有什么文件**，后半句看**历史里谁改过这个文件**。

### 前半：`ls */README.md`

| 部分 | 含义 |
|---|---|
| `ls` | 列出文件（不递归，只看给定的路径） |
| `*/README.md` | 通配符。`*` 只匹配**一层**目录名，bash 在把命令交给 `ls` **之前**就把它展开成 `子目录/README.md` |
| `;` | 命令分隔符。**无论前一句成功还是失败**都继续执行下一句（对比 `&&`：前一句失败就停） |

要点：
- 真正被 `ls` 执行的是展开后的路径列表，例如 `a/README.md b/README.md`。若一个都没匹配上，bash 默认把 `*/README.md` 原样传过去，`ls` 就报 `No such file or directory`。
- `*` 不匹配 `子目录/更深/README.md`，需要 `**/README.md`（且要开 `globstar`）。
- 它看的是磁盘现状，**不管 git 是否跟踪/忽略**。

### 后半：`git log --oneline -3 -- README.md`

| 部分 | 含义 |
|---|---|
| `git log` | 打印提交历史 |
| `--oneline` | 每个提交压成一行：短 hash + 标题 |
| `-3` | 最多 3 条（等价于 `-n 3`） |
| `--` | 选项结束标记。它**后面**的内容一律当路径，防止文件名被误认成选项 |
| `README.md` | 路径过滤器（pathspec）：只显示**动过这个文件**的提交 |

要点：
- 没有 `--` 时也能写 `git log -3 README.md`，两者结果一样；`--` 是为了消歧，更安全。
- 加了路径过滤后，`--oneline` 看到的还是提交标题，但该提交可能改了别的文件 —— 想知道只改了这个文件的哪几行，再配 `-p`。

### 一句话总结

`ls */README.md` = 现在有哪些子目录里有 README；`git log -3 -- README.md` = 这些（以及别处的）README.md 最近 3 次是被哪几次提交改的。两句之间用 `;` 只是「顺路都跑一遍」，没有依赖关系。

> 注意：`*/`、`;`、`&&` 是 bash 语法。在 PowerShell 里 `;` 可用，但通配符展开和 `&&` 行为不同，别混用。

---

## Q2. `find -r */*.md exec grep answer -l {}\;` 对吗？`-exec` 是什么？

### 三个同名的 "exec"

| 名字 | 是什么 | 谁实现 |
|---|---|---|
| `execve(2)` | 内核系统调用，用新程序**替换**当前进程 | 内核 |
| bash 内置 `exec` | bash 自己执行，替换 bash 进程，后续脚本行不再跑 | bash |
| `find -exec` | **find 的一个动作**，对每个匹配文件 fork + execve 一次 | find |

`find` 有自己的筛选/动作小语言（`-name` `-path` `-type` 筛选，`-print` `-exec` `-delete` 动作），与 bash glob 无关 —— 所以 `-name '*.md'` 反而**必须加引号**，防止 bash 抢先展开。

### 原命令的 4 处错（均已实测）

| 写法 | 错在哪 | 实测报错 |
|---|---|---|
| `-r` | find 没有 `-r` 选项 | `find: unknown predicate '-r'` |
| `*/*.md` | 被 shell 展开成路径列表当搜索起点；`*` 只覆盖一层 | `find: paths must precede expression` |
| `exec` | 少一个 `-` | — |
| `{}\;` | `{}` 与 `;` 之间要有空格；`;` 要转义 | `find: missing argument to '-exec'` |

修正：`find . -name '*.md' -exec grep -l answer {} +`

### `-exec` 的用法

`{}` 是当前文件路径的占位符，**终结符**决定启动几次：

| 写法 | 行为 | 实测 |
|---|---|---|
| `-exec cmd {} \;` | 每个文件启动一次，退出码还当筛选条件 | 4 行，每行一个路径 |
| `-exec cmd {} +` | 攒成一批启动一次（更快，推荐，放末尾） | 1 行，4 个路径 |

`\;` 形式可当条件用：`find . -name '*.md' -exec grep -q xxx {} \; -print` 只打印命中的文件。

### 更短的替代

`grep -rl answer --include='*.md' .` 或 `git grep -l answer -- '*.md'`。

### 踩坑提醒

在 PowerShell 里写 `-name "*.md"` 会被外层吃掉引号 → bash 抢先展开 → `find: paths must precede expression`。要写成 `-name \"*.md\"` 或 `-name '*.md'`。**先问「哪一层吃掉了引号」。**

---

## Q3. `find` 属于什么？shell language 还是 shell command？

`find` 是**外部程序**（external command / POSIX 说的 utility），不是 shell 语言的一部分。

在提示符下「能敲的东西」分四类（实测 `type`）：

| 类别 | 谁实现 | 例子 | 算 shell 语言吗 |
|---|---|---|---|
| shell 关键字 keyword | bash | `if` `for` `[[` `time`（`type if` → shell keyword） | ✅ 是语法 |
| 内置命令 builtin | bash 自己 | `cd` `echo`（`type cd` → shell builtin） | 半是：bash 实现，但不是语法 |
| 外部程序 | 独立可执行文件，靠 PATH 查找 | `find` `ls` `grep` `git`（→ `/usr/bin/find`、`/mingw64/bin/git`） | ❌ 不是 |
| alias / 函数 | 用户自己定义 | — | 可选 |

### 心法：**每一层程序有自己的解析器**

```mermaid
flowchart LR
  A["你敲的一整行"] --> B["bash 解析：引号 / 管道 / 重定向 / 展开"]
  B --> C["token 序列<br/>find . -name *.md -exec grep -l x {} +"]
  C --> D["find 用自己的语法解析：<br/>路径 / 筛选 / 动作"]
```

`-name`、`{}`、`+`、`-exec` 这些词在 bash 眼里只是**普通字符串**，bash 完全不懂；反过来 `|`、`>`、`*` 在 find 眼里也毫无意义（所以 find 的 `'*.md'` 要加引号）。

所以：
- 「shell 语言」= 只有 bash 认识的语法（引号、`;` `&&` `|`、`>`、`$var` `$(...)`、glob、`if/for/while`、函数）
- 「外部命令」= 被 bash 启动的程序，**各自定义自己的参数语法**（find、git 都是这种「带方言的程序」）
- 「shell command」口语上指「提示符下敲的整行」，严格说应叫命令行

### 附带陷阱

`type -a find` 输出里有 **两个** find：`/usr/bin/find`（Git 带来的 GNU find）和 `/c/Windows/system32/find`（Windows 自带的，语法完全不同）。PATH 顺序决定用哪个 —— 同名不同物。
