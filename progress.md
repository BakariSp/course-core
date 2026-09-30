# 学习进度

> 由 `python study.py status` 生成，不要手改。课程改 `progress/course.yaml`；进度和答题记录在数据库里。
> 更新于 2026-09-30 16:14:18

## 课程进度

| 学科 | 完成 | 单元 |
|---|---|---|
| `tools` 开发工具 | 2/5 | ✅ Shell · ✅ Git 与版本控制 · 🟡 调试与性能分析 · ⬜ 代码质量 · ⬜ Agentic Coding |
| `test` 测试 | 0/2 | 🟡 Getting Started Testing 演讲 · ⬜ pytest 入门教程 |
| `net` Web 与网络 | 0/4 | 🟡 第 1 章 概述 · 🟡 第 2 章 应用层与 HTTP · ⬜ 第 3 章 传输层与 TCP · ⬜ SSE 与 WebSocket（补充） |
| `db` 数据库 | 0/5 | ⬜ SQLBolt 全部练习 · ⬜ 15-445 索引 · ⬜ 15-445 事务与隔离级别 · ⬜ 15-445 并发控制 · ⬜ 15-445 日志与恢复 |
| `os` 操作系统 | 0/4 | ⬜ 进程 · ⬜ 线程 · ⬜ 同步：锁与条件变量 · ⬜ 文件系统与崩溃一致性 |
| `sec` 安全 | 0/5 | ⬜ 访问控制 · ⬜ 身份认证 · ⬜ 路径穿越 · ⬜ SSRF · ⬜ 多租户隔离审查（项目实战） |
| `dist` 分布式与数据系统 | 0/1 | ⬜ Kleppmann 讲座 |
| `design` 软件设计 | 0/2 | ⬜ Ousterhout 演讲 · ⬜ 分层、端口与依赖注入 |
| `sysdesign` 系统设计 | 0/1 | ⬜ ByteByteGo 视频 |
| `agent` Agent 工程 | 0/2 | ⬜ Building Effective Agents · ⬜ 验收项目：问题 → 知识点 → 掌握度 |
| `lm` 学习者建模 | 0/1 | ⬜ 学习者模型：掌握度与复习排程 |
| `dsa` 数据结构与算法 | 0/1 | ⬜ NeetCode |
| `lang` 语言深度 | 0/2 | ⬜ import asyncio 系列 · ⬜ react.dev 状态管理 |

## 概念掌握度

等级：1 记忆 → 2 理解 → 3 应用 → 4 分析。某一级最近 3 次平均分 ≥ 70% 算通过。

| 概念 | 掌握 | 练习次数 | 最近 | 提示 |
|---|---|---|---|---|
| `net.cmd.ls` ls | ███░ 3 应用 | 1 | 2026-09-28 |  |
| `net.overview.internet` the Internet（因特网） | ░░░░ 0  | 1 | 2026-09-28 | 第 1 级有错 |
| `net.overview.layer` protocol layering / protocol stack（协议分层 / 协议栈） | ░░░░ 0  | 1 | 2026-09-28 | 第 2 级有错 |
| `net.overview.packet_switching` packet switching（分组交换） | ███░ 3 应用 | 1 | 2026-09-28 |  |
| `net.overview.protocol` protocol（协议） | ░░░░ 0  | 1 | 2026-09-28 | 第 1 级有错 |
| `net.overview.throughput` end-to-end throughput / bottleneck link（端到端吞吐量 / 瓶颈链路） | ████ 4 分析 | 2 | 2026-09-28 |  |
| `net.overview.transmission_delay` transmission delay = L/R（传输时延） | ░░░░ 0  | 1 | 2026-09-28 | 第 2 级有错 |
| `tools.cmd.git.bisect-run` git bisect run | ████ 4 分析 | 1 | 2026-09-28 |  |
| `tools.cmd.git.blame` git blame | ████ 4 分析 | 1 | 2026-09-28 |  |
| `tools.cmd.git.diff` git diff | ░░░░ 0  | 1 | 2026-09-28 | 第 2 级有错 |
| `tools.cmd.git.init` git init | █░░░ 1 记忆 | 1 | 2026-09-28 |  |
| `tools.cmd.git.reset` git reset <路径> | ███░ 3 应用 | 1 | 2026-09-28 |  |
| `tools.git.conflict` 合并冲突与冲突标记 | ███░ 3 应用 | 1 | 2026-09-28 |  |
| `tools.git.head` HEAD | ██░░ 2 理解 | 1 | 2026-09-28 |  |
| `tools.git.staging` 暂存区（staging area） | ██░░ 2 理解 | 1 | 2026-09-28 |  |
| `tools.shell.exitcode` 退出码与 $? | ░░░░ 0  | 1 | 2026-09-26 | 第 2 级有错 |
| `tools.shell.expansion` shell 先改写命令行再启动程序 | ░░░░ 0  | 1 | 2026-09-26 | 第 2 级有错 |
| `tools.shell.glob` 通配符（glob） | ░░░░ 0  | 2 | 2026-09-26 | 第 4 级有错 |
| `tools.shell.pipe` 管道（|） | ███░ 3 应用 | 1 | 2026-09-26 |  |
| `tools.shell.quoting` 引号改变谁来解释 | ░░░░ 0  | 1 | 2026-09-26 | 第 2 级有错 |
| `tools.shell.redirection` 重定向（> / >> / <） | ░░░░ 0  | 1 | 2026-09-26 | 第 2 级有错 |
| `tools.shell.stdstreams` 标准输入与标准输出 | █░░░ 1 记忆 | 1 | 2026-09-26 |  |

## 最近测验

| 测验 | 最近一次 | 已判分题数 | 平均分 |
|---|---|---|---|
| net/01-overview | 2026-09-28 | 8 | 57% |
| tools/02-git | 2026-09-28 | 8 | 84% |
| tools/01-shell | 2026-09-26 | 8 | 55% |

## 待批改

无。
