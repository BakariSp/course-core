# 每种题怎么写

公共字段：`id`（q1、q2……）、`checker`、`concept`、`level`、`prompt`（支持 Markdown：代码块、行内代码、粗体）、可选的 `kind`（bug / project），以及 `key`：

- `choice`：`options`（选项文字，不带字母）、多选写 `multi: true`；`key.answer` 是字母（多选用列表）。
- `fill`：题干里每个空写成 `____`；`key.blanks` 是每个空可接受的答案（一组一个列表，比较时忽略大小写和多余空格）。
- `code`：`file`（`code/ex1.py`，每道题一个文件）、`starter`（初始代码：只有函数签名、docstring 和 `raise NotImplementedError`）、
  `tests`（pytest 判题测试：`from ex1 import ...`；未实现时必须是红的；覆盖边界情况，测试名说清楚在测什么，学习者只看得到测试名和报错）；
  `key.solution` 是参考实现（环境会用它验证测试能全部通过）。只用标准库和 pytest。
- `terminal`：受限的练习终端，跑真实的 git。`setup` 是搭场景的命令（学习者看不到）；`key.checks` 是做完后仓库应该满足的检查
  （`run` 一条 git 命令看输出，或 `file` 看文件内容；再加 `equals` / `contains` / `not_contains` / `matches` / `line_count` 之一，和 `desc`）；
  `key.solution` 是参考做法（按顺序敲的命令）。练习终端里**只能用**：大部分 git 子命令，以及 `ls`、`cat`、`echo 文本 > 文件`（`>>` 追加）、`touch`、`rm`、`mkdir`、`pwd`；
  **没有**管道、`cd`、编辑器和其他程序；`git config`、`push/pull/fetch/clone`、`bisect run`、`rebase --exec` 被屏蔽。环境会在这个终端里按参考做法真的敲一遍。
- `short`：`key.rubric` 是批改要点，每条末尾写这一条的分值，如「指出 cd 失败后脚本没有停（0.5）」，加起来是 1；不写分值的是加分项。
- 每道题的 `key.explain`：交卷后显示的解析 / 标准答案。

# 得分点：每个得分点考哪个知识点（`key.parts`）

除代码题外，每道题的 `key.parts` 和这道题的得分点**一一对应、个数相同**：`choice` 每个选项一个、`fill` 每个空一个、`short` 每条 rubric 一个、`terminal` 每条 check 一个。代码题不写。

每个得分点写：
- `concept`：这个选项 / 空 / 评分点 / 检查项真正考的知识点 id（可以和这道题的 `concept` 不同。比如一道讲"一个分组怎么走"的多选题，讲转发的选项写转发的知识点，讲排队的选项写排队时延的知识点）。
- `level`（可选）：和这道题的等级不同时才写。
- `misconception`（可选）：学习者**没拿到**这个得分点时，最可能是哪种误解。选择题的干扰项、填空里容易填错的空，都应该写。
  用知识点列表里「已有误解」的 id；没有合适的，就在整套题的 `misconceptions` 里提出一个新的：

```json
"misconceptions": {"net.overview.layer": {"transport_vs_link": "把运输层（进程到进程）和链路层（相邻设备之间传 frame）弄反"}}
```

误解 id 用小写字母、数字、下划线；说明写成"把 X 当成 Y"这样具体的一句，写的是学习者脑子里的错误想法，不是"没记住"。
