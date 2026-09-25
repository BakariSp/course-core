# 课程清单

原则：**验证能力优先**。能帮你判断 AI 代码对错的排前面，数据结构与算法刻意靠后。
形式：以视频课为主，书只在没有好视频时当参考；每节课后让 AI 出题。

> 课程链接和讲次划分请在开课前自己核对一遍（课程会改版）；"看哪几讲"是建议，不是定论。

## 学科与资源

| id | 优先级 | 学科 | 主课（视频/交互） | 看哪部分 | 学到什么程度 |
|---|---|---|---|---|---|
| `tools` | P0 | 开发工具 | MIT *The Missing Semester of Your CS Education*（[2026 版官网](https://missing.csail.mit.edu/2026/)，[YouTube 播放列表](https://www.youtube.com/playlist?list=PLyzOVJj3bHQunmnnTXrNbZnBaCA-ieK4L)） | [shell](https://missing.csail.mit.edu/2026/course-shell/)、[git / version control](https://missing.csail.mit.edu/2026/version-control/)、[调试与性能分析](https://missing.csail.mit.edu/2026/debugging-profiling/) | 能自己 `git bisect`、看日志、用调试器 |
| `test` | P0 | 测试 | Ned Batchelder, *Getting Started Testing*（[PyCon 2014 演讲](https://www.youtube.com/watch?v=FxSsnHeWQBY)）+ [pytest 官方教程](https://docs.pytest.org/en/stable/getting-started.html) | 全部 | 能判断一个测试有没有真的在测东西 |
| `net` | P0 | Web 与网络 | Kurose & Ross 作者本人的视频讲座（[课程页](https://gaia.cs.umass.edu/kurose_ross/online_lectures.htm)，配套 *Computer Networking: A Top-Down Approach*） | 第 1-3 章（应用层、HTTP、TCP） | 能讲清一次聊天请求从浏览器到 LLM 再回来的全过程，懂 SSE / WebSocket |
| `db` | P0 | 数据库 | [SQLBolt](https://sqlbolt.com/)（交互练习）→ [CMU 15-445（Andy Pavlo，Fall 2024 播放列表）](https://www.youtube.com/playlist?list=PLSE8ODhjZXjYDBpQnSymaectKjxCy6BYq) | 15-445 只挑索引、事务、并发控制、日志恢复几讲 | 懂事务、索引、隔离级别、为什么需要迁移 |
| `os` | P1 | 操作系统 | UC Berkeley [CS162 播放列表](https://www.youtube.com/playlist?list=PLF2K2xZjNEf97A_uBCwEl61sdxWVP7VWC)（YouTube） | 进程、线程、同步（锁/条件变量）、文件系统 | 懂进程与线程、锁、文件系统的原子性 |
| `sec` | P1 | 安全 | [PortSwigger Web Security Academy](https://portswigger.net/web-security)（免费，带在线靶场） | [访问控制](https://portswigger.net/web-security/access-control)、[身份认证](https://portswigger.net/web-security/authentication)、[路径穿越](https://portswigger.net/web-security/file-path-traversal)、[SSRF](https://portswigger.net/web-security/ssrf) | 能审查多租户隔离有没有漏洞 |
| `dist` | P1 | 分布式与数据系统 | Martin Kleppmann, *Distributed Systems*（[剑桥讲座播放列表](https://www.youtube.com/playlist?list=PLeKd45zvjcDFUEv_ohr_HdUFe97RItdiB)）；[DDIA 官网](https://dataintensive.net/)当参考书 | 全部（约 8 小时） | 能在存储、一致性、扩展方案之间做取舍 |
| `design` | P1 | 软件设计 | John Ousterhout, *A Philosophy of Software Design*（[Talks at Google 演讲](https://www.youtube.com/watch?v=bmSAYlu0NcY)） | 全部 | 能看出"浅模块""信息泄漏"这类 AI slop |
| `sysdesign` | P2 | 系统设计 | [ByteByteGo 频道](https://www.youtube.com/@ByteByteGo)（YouTube） | 挑缓存、队列、限流、多租户相关的视频 | 能画出你的产品扩到 1 万用户的架构 |
| `agent` | P2 | Agent 工程 | Anthropic [《Building Effective Agents》](https://www.anthropic.com/engineering/building-effective-agents)（短文）；Hamel Husain & Shreya Shankar 的 [evals 课程](https://maven.com/parlance-labs/evals)（Maven，收费，可选） | — | 能和 cofounder 一起设计 eval 和护栏 |
| `dsa` | P2 | 数据结构与算法 | [NeetCode](https://www.youtube.com/@NeetCode)（YouTube）+ [NeetCode 150 路线图](https://neetcode.io/roadmap)（[练习页](https://neetcode.io/practice)） | 挑 60-80 道 easy/medium：哈希、树、图、堆 | 能看出 AI 写了 O(n²) 这类复杂度问题 |
| `lang` | P3 | 语言深度 | Łukasz Langa 的 [*import asyncio* 系列](https://www.youtube.com/playlist?list=PLhNSoGM2ik6SIkVGXWBwerucXjgP1rHmB)（YouTube）；[react.dev/learn](https://react.dev/learn) 官方文档自带的挑战题 | asyncio、React 状态管理 | 看得懂 async 的坑和前端状态管理的问题 |

## 用 nanoteacher 当题库

每学一个概念，就在项目里找它出现的地方。下面这些规则来自 `D:\nanoteacher\CLAUDE.md`，每条背后都是一个知识点：

| 项目规则 | 背后的知识点 | 学科 |
|---|---|---|
| 线上 4 个 worker，跨请求的状态不能放进程内字典 | 进程、内存隔离 | `os` |
| `atomic_write_text` = 临时文件 + rename | 文件系统原子性、崩溃一致性 | `os` |
| `_student_lock` | 锁、竞态条件 | `os` |
| SQLite WAL 模式扛不住 100 以上并发 | 事务、隔离级别、写锁 | `db` |
| JSON 要带 `schema_version`，字段只增不删 | 数据演进、向后兼容 | `dist` |
| 重启会断开所有 SSE 连接 | 长连接、部署策略 | `net` |
| OSS 预签名 URL 会过期 | 签名与时效性鉴权 | `sec` |
| topic id 校验、`is_relative_to()` | 路径注入 | `sec` |
| `"current"` 是 session_id 的魔法值（issue #78） | 数据建模、哨兵值的坏处 | `design` |
| Session JSONL 是 source of truth，SSE 只是预览 | 单一数据源、事件日志 | `dist` |

## 阶段

| 阶段 | 时长 | 学习 : 项目 | 学科 | 项目上做什么 | 过关标准 |
|---|---|---|---|---|---|
| 1. 能验证 | 第 1-8 周 | 50 : 50 | 所有 P0 | 给核心路径补测试；按红→绿修 bug；每个 PR 自己写 Test plan | 能独立定位并修复一个线上 bug，全程自己验证 |
| 2. 能判断 | 第 9-16 周 | 40 : 60 | P1 | 端到端负责一个模块的重构：自己写设计文档，AI 只负责实现 | 能写出一篇 ADR，列出 2-3 个方案和取舍，经得起追问 |
| 3. 能交付 | 第 17-24 周以后 | 30 : 70 | P2，加上用到什么学什么 | 把 SOP 文档化；和 cofounder 建立 eval 与灰度流程 | 能独立判断大改动的架构；连续几次晋升复盘都是绿灯 |

## 工程实践：验证阶梯（贯穿所有阶段）

| 层 | 拦住什么问题 | 实践 |
|---|---|---|
| 1. 契约先行 | AI 理解错了需求 | 先写数据模型、API 字段、状态机、不变量，再让 AI 实现 |
| 2. 类型 | 字段拼错、传错参数 | TS strict；Python 加 type hints + pyright |
| 3. 测试 | 逻辑错误、回归 | 先写一个会失败的测试，再修（红 → 绿） |
| 4. 小 diff + 审查 | 顺手乱改、过度设计 | 一个 PR 只做一件事；读 diff；用另一个会话审查 |
| 5. CI | "在我电脑上能跑" | 测试和类型检查不过就不合并 |
| 6. 分环境部署 | 直接炸生产 | staging → main、灰度开关、回滚方案 |
| 7. 可观测性 | 出了问题不知道 | 结构化日志、埋点、告警、晋升复盘 |

Agent 产品额外要做：eval 集、prompt 版本化、工具调用白名单、token / 步数 / 超时配额。
多租户额外要做：数据与文件按租户隔离、每个请求都在后端鉴权、限流防止一个租户拖垮所有人。
