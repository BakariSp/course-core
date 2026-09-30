# 课程覆盖度审计（F-107，2026-09-30）

> **2026-09-30 修正（F-108）**：这一版把重心放在运维（发布、灰度、观测）上，学习者指出应当从下往上先学开发与架构设计。修订后的提议见 DECISIONS.md 的 D-068；第 3 节的对照表仍然有效，第 4 节的单元建议以 D-068 为准。

> 起因：Hello Interview 的 System Design Core Concepts 有 9 项，课程里只覆盖 2–3 项，学习者怀疑课程的合理性。
> 学习者补充：目标不只是从已知的错误往回推，而是**能和 AI 一起开发并上线一个稳定的、agent native 的学习系统（core）**；
> 学习者还不会软件工程，要结合背景、遇到过的问题（DECISIONS.md）一起查。

## 1. 怎么查的

一个主题该不该学，用三把尺子量，任意一把量出"要"就算缺口候选：

| 尺子 | 问的问题 | 来源 |
|---|---|---|
| A. 目标 | "开发并上线一个稳定的 agent native 学习系统"需要这一块吗？ | `course.yaml` 的 goal / destination，`profile.yaml` |
| B. 遇到的问题 | nanoteacher 或 cs-study 里已经在用、已经出过事、或你已经在拍板相关的设计吗？ | nanoteacher 的代码、CLAUDE.md、灰度清单；本仓库 DECISIONS.md |
| C. 公认大纲 | 业内公认的软件工程知识体系里有这一块吗？ | SWEBOK v4（IEEE 软件工程知识体系，2024 版）的知识域；Hello Interview Core Concepts；课程自己写的「验证阶梯」（`docs/curriculum-notes.md`） |

每一项的结论只有三种：**已覆盖**（路线上有单元）、**缺**（要补）、**故意不学**（写明理由）。

## 2. 结论

1. **课程不是整体不合理，是少了整整一个"上线与运行"的阶段。**
   现在的路线覆盖的是"一次请求怎么走通"：网络 → 后端 → 存储 → agent，外加安全和测试。
   但"上线一个**稳定**的系统"还需要：怎么发布（CI、分环境、灰度、回滚）、坏了怎么知道（日志、指标、告警）、
   外部依赖出错怎么办（超时、重试、幂等）、数据结构怎么演进（迁移、schema_version）。
   这些 nanoteacher 全都在用，你也在 cs-study 里反复拍板，但路线上**一个单元都没有**。
2. **课程自己写的原则没有落到单元里。** `curriculum-notes.md` 的验证阶梯有 7 层，
   路线只覆盖第 3 层（测试）和第 4 层（git），第 2 层（类型检查）部分覆盖；
   第 1 层契约先行、第 5 层 CI、第 6 层分环境部署、第 7 层可观测性都没有单元。
3. **System design 那一页里，真正缺、而且现在就用得上的是缓存、API 设计和数据建模**；
   分片、一致性哈希、CAP 对单机 SQLite 的产品来说，学到概念层就够（能说出"什么时候需要它"）。
4. 你截图里的《结构化程序设计》（Dijkstra、Hoare、Dahl，1972）讲的是软件工程最底层的两件事：
   **抽象 + 分层分解**（设计），**规格 + 不变量 → 可证明正确**（正确性）。前一件路线上有 Ousterhout；
   后一件（规格、前后置条件、不变量、契约）**没有**。而它恰好是"验证 AI 写的对不对"的根：
   没有规格就没有"对"的定义。nanoteacher 的 CLAUDE.md 要求写 `INVARIANT:` 注释，用的就是这个概念。

## 3. 逐块对照

### 3.1 软件工程知识体系（SWEBOK v4 的知识域）

| 知识域 | 课程现状 | A 目标 | B 遇到的问题 | 结论 |
|---|---|---|---|---|
| 需求 | 无单元 | 要 | 你是产品经理，写 PRD 是本职 | **故意不学**：已有能力 |
| 架构 | design-02-layers | 要 | cs-study 分层、`test_architecture.py`；D-023 三层 | 已覆盖 |
| 设计 | design-01-ousterhout | 要 | 浅模块、信息泄漏（AI slop） | 已覆盖 |
| **构造：规格、不变量、契约** | 无 | 要（"对"的定义） | nanoteacher `INVARIANT:` 注释；D-056 得分点挂知识点；验证阶梯第 1 层 | **缺** |
| 测试 | test-01、test-02 | 要 | 修 bug 必须带红→绿测试；D-060 测试原则 | 已覆盖 |
| **运行（发布、监控、事故）** | 无 | 要（"上线""稳定"） | nanoteacher：`gate-pr.yml`、`e2e.yml`、`load-test.yml`；staging → main 晋升、hotfix 当天 cherry-pick；灰度清单 + 秒级回滚开关；`_events.*.jsonl` 观测；重启断 SSE 的生产禁令 | **缺（最大的缺口）** |
| **维护：数据与接口演进** | 无 | 要 | JSON 带 `schema_version`、字段只增不删；nanoteacher 一串 `test_*_migration.py`；D-047 数据结构、D-025 课程版本 | **缺** |
| 配置管理 | tools-02-git | 要 | D-059 分支和半线性历史 | 已覆盖 |
| 质量（类型检查、静态分析） | tools-04-quality | 要 | 验证阶梯第 2 层 | 已覆盖 |
| 安全 | sec-01 ~ sec-05 | 要 | 多租户、路径注入、OSS 签名 | 已覆盖 |
| 管理、过程、经济 | 无 | 弱 | PM 本职；D-045 模型费用记账 | **故意不学** |
| 计算基础（OS、网络、数据结构） | os、net、dsa | 要 | 4 个 worker、锁、SSE | 已覆盖 |
| 数学基础 | 无 | 弱 | 学习者模型的算法要一点概率 | 放进 lm-01 按需补，不单列 |

### 3.2 可靠性：外部依赖（LLM、支付、微信）出错时

这一块 SWEBOK 归在"运行"里，单列出来是因为 **agent 产品的稳定性主要坏在这里**：LLM 慢、超时、断连、重复输出，而且每次重试都花钱。

| 概念 | nanoteacher 里的实例 | cs-study 里的实例 | 结论 |
|---|---|---|---|
| 超时与重试（什么时候**不该**重试） | `engine/litellm_resilience.py`：连接错误重建连接池，超时故意不重试（"会重复请求"） | 备课循环的重试和预算上限（D-035） | **缺** |
| 幂等（重复执行结果不变） | `engine/database.py` 的幂等账本（`event_id` 主键）；首次学习奖励 "idempotent"；Stripe 退款 webhook | D-063 断了自动接着备 | **缺** |
| 后台任务与队列 | `usage_worker.py`、`weixin_poller.py` | D-041 出题在后台跑、D-040 提前备课 | **缺** |
| 限流、防止一个租户拖垮所有人 | 验证码限流 `test_challenge_rate_limit.py` | — | **缺**（验证阶梯"多租户额外要做"里写了） |

### 3.3 System design（Hello Interview Core Concepts）

| 小节 | 课程现状 | 结论 |
|---|---|---|
| 网络（HTTP、WebSocket、SSE、gRPC、负载均衡） | net-02、net-03、net-04 | 已覆盖（负载均衡在 sysdesign 里顺带讲） |
| API 设计（REST、分页、鉴权、限流） | 鉴权在 sec-02 | **缺**：分页、限流、错误约定、幂等键 |
| 数据建模（关系型和 NoSQL、范式、按访问模式设计） | db-01 只练查询 | **缺**：你在 D-047、D-056 已经在做数据建模决定 |
| 索引 | db-02-index | 已覆盖 |
| 缓存（旁路缓存、失效、缓存击穿、CDN） | 无 | **缺**：F-094 课程页从 0.6s 降到 0.13s，用的就是"读一次、有新证据才重读"，这就是缓存失效 |
| 分片、一致性哈希 | 无 | **概念层**：能说出"什么时候单机 SQLite 不够、下一步是什么" |
| CAP / PACELC | 无（dist-01 没定范围） | **概念层**，深入留给 dist-01 |
| Numbers to Know（延迟、容量量级） | 无（付费） | **缺**：X2 要"量出各段耗时"，需要一张量级表当参照 |

### 3.4 Agent native 特有的

| 概念 | 课程现状 | 结论 |
|---|---|---|
| prompt 组装、上下文、工具、评测 | agent-01、tools-05、agent-02 | 已覆盖 |
| prompt 与模型的版本化、灰度 | 无 | 并入"上线与运行"：nanoteacher 灰度清单几乎都是 prompt / 模型行为改动（qwen 思维链泄漏、复读终止闸） |
| 成本与配额（token、步数、超时） | 无 | 并入"可靠性"：验证阶梯"Agent 产品额外要做"里写了，D-045 在记账 |

## 4. 建议补的单元（候选，材料备课前要核对）

按缺口大小排。每个都能在 nanoteacher 或 cs-study 的真实代码上验收。

| 新单元 | 补哪块 | 验收（在真实代码上） | 候选材料 |
|---|---|---|---|
| `se-01-spec` 规格、不变量与契约 | 构造 / 验证阶梯第 1 层 | 给 cs-study 的一个函数写出前置条件、后置条件、不变量，再判断一份 AI 实现有没有违反 | MIT 6.031 Software Construction 的 Specifications、Abstract Data Types 两篇（在线读物）；你截图里的书当背景 |
| `ops-01-deliver` 发布：CI、分环境、灰度、回滚 | 运行 / 阶梯第 5、6 层 | 讲清 nanoteacher 一个改动从 PR 到生产经过哪几道闸（`gate-pr.yml` → staging → main），读一份灰度清单说出回滚按钮在哪 | Google SRE 书的 Release Engineering 章；nanoteacher 自己的灰度清单 |
| `ops-02-observe` 可观测性：日志、指标、告警 | 运行 / 阶梯第 7 层 | 用 nanoteacher 的 `_events` 日志回答"上次改动之后，复读少了吗" | Google SRE 书的 Monitoring 章 |
| `ops-03-reliability` 超时、重试、幂等、后台任务 | 运行 / 3.2 节 | 读 `litellm_resilience.py`，说出为什么超时不重试；在幂等账本上说出重复投递时发生什么 | 待找（候选：Stripe 关于幂等键的工程博客、AWS Builders' Library 的超时与重试） |
| `sysdesign-01-api-data` API 设计 + 数据建模 + schema 演进 | 3.3 节 + 维护 | 评一次 cs-study 的数据结构改动（比如 D-047）：访问模式、范式、老数据怎么兼容 | Hello Interview 的 API Design、Data Modeling 两节；DDIA 第 2、4 章（参考） |
| `sysdesign-02-scale` 缓存 + 扩展概念 | 3.3 节 / X2 | 说出 nanoteacher 扩到 1 万用户先坏哪里，哪里加缓存、失效怎么做；分片和 CAP 能说出什么时候需要 | Hello Interview 的 Caching、Sharding、Consistent Hashing、CAP 四节；ByteByteGo 当补充 |

终点地图的变化：X2 由 `sysdesign-02` 撑起来；新增一个横切面 **X4 上线与稳定**（`ops-01 ~ 03`）；
X3 验证加上 `se-01-spec`。

放在路线的哪里（提议）：
- `se-01-spec` 放在「动手与验证」之后、「后端结构与运行」之前：它是"对"的定义，后面每个单元都用得上。
- `sysdesign-01-api-data` 紧跟「从请求到数据：HTTP 与 SQL」（刚学完请求和表，接着问"怎么设计它们"）。
- `ops-03-reliability` 放在「长连接与推流」之后（要先懂 async 和连接）。
- `sysdesign-02-scale` 放在「数据库进阶」之后（要先懂索引、事务）。
- `ops-01`、`ops-02` 放在「AI 产品层」之前：发布和观测 prompt 改动的例子都在 agent 那边。

## 5. 故意不学（写进 course.yaml，以后可查）

| 主题 | 理由 |
|---|---|
| 需求工程、项目管理、过程模型、软件经济 | 产品经理本职 |
| 编译原理、形式化证明 | 验证 AI 代码用测试、类型、规格就够，不需要形式化证明 |
| 分片、一致性哈希、CAP 的实现细节 | 单机 SQLite 产品，只学到"什么时候需要它" |
| 前端工程化（打包、构建工具） | 暂不学；等 S1 界面单元暴露问题再说 |

## 6. 这份审计本身的局限

- 尺子 C 只用了 SWEBOK v4 和一份 system design 大纲，没有对照大学的软件工程课表；以后发现新的缺口，按同样的三把尺子补进这张表。
- 候选材料只核对了名字和出处，没有逐个看过内容，备课前要核对。你偏好视频，这几块好的视频少，大多是读物。
