# cs-study 数据模型：从现有数据映射到新模型

前序：[data-requirements.md](./data-requirements.md)（为什么这样分）
后续：[backend-core-design.md](./backend-core-design.md)（代码分层；§5 以本文为准）
状态：提议（D-021 ~ D-023），2026-09-25

本文做三件事：
1. 把现有的**每一种学习记录**逐条映射到统一证据信封（§1），找出映射不上的地方（§2）；
2. 把 agent 运行目录里的**每一个文件**映射到 harness 对象（§3）；
3. 由映射推出**逻辑表结构**，并说明哪些留在文件里（§4-§6）。

符号：🟢 直接映射　🟡 要补字段或换算　🔴 现在的数据有问题，映射时要处理

---

## 1. 学习证据 → 统一信封

信封（见 data-requirements §9）：`id · ts · actor{type,id,role,variant} · learner · verb · object{type,id,version} · context{unit,plan,section,caused_by,run} · result{score,ok,pending} · nodes[] · payload{}`

**verb 属于哪一层**：core 的 verb 学什么都有；spec 的 verb 由学科 spec 注册（现在都是 `cs-practice`）。

### 1.1 `attempts.jsonl`（作答）

| 现在 | verb | 层 | object | actor | result | payload | 状态 |
|---|---|---|---|---|---|---|---|
| 网页提交（`server.submit`） | `answered` | core | `question` · `<lesson>#<qid>` · version = 题目内容哈希 | learner:me | score / ok / pending | `checker, level, response, feedback, detail, runs[]` | 🟡 补题目版本、补 `runs[]`（§2 ①） |
| 批改（`study.py grade`，带 `supersedes`） | `graded` | core | 同上 | agent:tutor（variant = `CLAUDE.md` 哈希） | score | `note`；`context.caused_by` = 被批改的那条 | 🟡 现在整条复制了原作答（response、concept、level），新模型只存批改本身 |
| 课外练习（`study.py log`，`quiz: "external"`） | `logged_practice` | core | `external` · `<来源>` | learner:me | score | `note` | 🟡 现在用 `quiz="external"`、`qid="-"` 冒充一道题 |

`concept` → `nodes: [concept]`；`level` → `payload.level`（它是题目的属性，不是证据的；有题目版本以后也可以从题目里查到，存一份是为了投影不必回读文件）。

### 1.2 `runs.jsonl`（代码运行）

| 现在 | verb | 层 | object | result | payload | 状态 |
|---|---|---|---|---|---|---|
| `kind: run`（点「运行测试」） | `ran_tests` | **cs-practice** | `question` + 版本 | score = passed/total | `kind, code, passed, total, failures[]` | 🟢 |
| `kind: submit`（提交时的那次运行） | `ran_tests` | cs-practice | 同上 | 同上 | 同上 + `submit: true` | 🟡 它和随后的 `answered` 是同一个用例写的，`answered.payload.runs[]` 引用它 |

### 1.3 `study_log.jsonl`（13 种学习事件）

| 现在的 `event` | 写者 | verb | 层 | object | result | payload | 状态 |
|---|---|---|---|---|---|---|---|
| `open` | 客户端 | `opened` | core | `section` · `<unit>#<i>` | — | — | 🟢；它触发的**练习场快照**是 system 动作，另记（§2 ④） |
| `done` | 客户端 | `completed` | core | section | — | `minutes` | 🟢 |
| `undone` | 客户端 | `uncompleted` | core | section | — | — | 🟢 |
| `skip` | 客户端 | `skipped` | core | section | — | — | 🟢 |
| `activity` | 客户端 | `pinged` | core | section 或 unit | — | `kind: page / video` | 🟢 |
| `term` · action=open | 客户端 | `viewed` | core | `node` · `<node id>` | — | — | 🟢 |
| `term` · action=known / unknown | 客户端 | `voted_term` | core | node | — | `vote: known / unknown` | 🟢 |
| `term_miss` | 客户端 | `flagged_unexplained` | core | section | — | `text` | 🟢 |
| `load_rating` | 客户端 | `rated_load` | core | section | — | `rating 1-5` | 🟢 |
| `checkpoint` | 服务器 | `answered` | core | `checkpoint` · `<unit>#<i>.<idx>` · version = plan id | score / ok | `type, response` | 🔴 `attempt`（第几次）和"整节通过时把本节新词也算进 nodes"都是**写入时算好的投影**（§2 ②③） |
| `hint` | 服务器 | `requested_hint` | core | checkpoint | — | `level` | 🟢；之后的 `answered` 用 `caused_by` 指回它（§2 ⑤） |
| `lab` · op=run | 服务器 | `ran_command` | **cs-practice** | `lab` · `<unit>` | ok = (rc == 0) | `cmd, rc, outside` | 🟢 |
| `lab` · op=restore / reset / fill | 服务器 | `restored_lab` / `reset_lab` / `filled_lab` | cs-practice | lab | — | — | 🟢 |
| `observation` | 服务器（导师 CLI） | `observed` | core | node | ok = polarity | `polarity, note` | 🔴 现在不记是谁观察的，也不记根据什么（§2 ⑥） |
| `manual` | 服务器（CLI） | `logged_time` | core | — | — | `start, minutes, note` | 🟢 |

**所有学习事件的 `context.plan`**：新事件写入时就有；旧事件由 `plan_of_event` 的规则在**导入时**补齐一次，之后不再现场推。

### 1.4 将来的 verb（验证信封够不够用）

| 功能 | verb | object | 要改信封吗 |
|---|---|---|---|
| 课程页提问（D-018）、讲解 agent 追问 | `asked` / `replied` | `message` · `<run>#<turn>`；`replied.payload.citations[]` = 材料切片 id | 否 |
| agent 提出策略 | `proposed_strategy` | `strategy` · `<id>`；payload = 范围、内容、依据的证据 id | 否 |
| 策略被验证 / 推翻 / 否决 | `confirmed_strategy` / `refuted_strategy` / `vetoed_strategy` | 同上 | 否 |
| 讲解稿读到哪 | `read` | `artifact` · `<id>#<段>` | 否 |
| 学日语：听写 | `answered`（检验器 = dictation，spec 注册） | question | 否 |

**策略记忆也是事件**：`proposed_strategy` → `confirmed_strategy` / `refuted_strategy` / `vetoed_strategy`。"当前有哪些已验证的策略"是投影。这样"带出处、可推翻的信念"和证据走同一套机制，不需要一张可以改的策略表。

---

## 2. 映射不上的地方（要在迁移时处理）

| # | 问题 | 现在的样子 | 处理 |
|---|---|---|---|
| ① | 代码运行对不上是哪次提交（G1） | 只能按时间猜 | `answered` 写入时，把"这道题上一次提交之后的所有 `ran_tests` id"写进 `payload.runs[]`。**只追加，不回填**。导入旧数据时按同一规则算一次 |
| ② | `checkpoint.attempt`（第几次作答）是写入时算的 | 存在事件里 | 新事件不存，由投影数；导入时丢弃（可以重算出来） |
| ③ | 整节通过时，把本节新词记成"会用"的证据 | 在写入时判断，塞进 `checkpoint.nodes` | 这是一条**推导规则**，不是事实。新模型：`answered` 的 `nodes` 只放这道题考的节点；"整节通过 ⇒ 新词也算会用"移到节点状态投影里，按规则版本重算。导入时把旧事件里多出来的新词节点去掉，交给投影 |
| ④ | `open` 的副作用：创建练习场快照 | 快照目录就是唯一记录 | 快照是 cs-practice 练习环境的状态，不是学习证据。记在 `practice_snapshot` 表（§4.1），文件仍在磁盘上。"不可重建"从此看得见 |
| ⑤ | 提示和随后的作答没有因果（G2） | 按时间窗数 | `answered.context.caused_by` = 这道检查点最近一次 `requested_hint` 的 id（服务器写入时查得到） |
| ⑥ | 观察不记是谁、根据什么（G4） | 只有 `node, polarity, note` | `actor` 必填；`payload.based_on[]` 可选，指向触发观察的证据或对话。导入的 6 条旧观察：actor = agent:tutor，variant 未知 |
| ⑦ | 题目版本（G3） | 没有 | 版本 = 这道题在 quiz.yaml 里的内容 + key.yaml 里它的答案的哈希（去掉空白差异）。导入时按**现在**的文件算，并标 `version_inferred: true`（旧作答当时的题目可能不一样，诚实标出来） |
| ⑧ | 批改记录复制了原作答 | `grade` 写了整条 | 新 `graded` 只存分数、备注、`caused_by`。导入时保留原 id，丢掉重复字段 |
| ⑨ | `learner.md` 里的散文 | 不是事件 | 逐条人工转换成 `observed`（知识状态）和 `proposed_strategy` + `confirmed_strategy`（已验证过的讲法，出处写"learner.md 2026-09-25"）。由 tutor 做，你过目 |

现在的数据量（study_log 23 行、runs.jsonl 几条、attempts 还没有）很小，导入可以一次做完、逐条核对。

---

## 3. agent 运行目录 → harness 对象

以 `runs/agents/tutor-prep/<run>/` 为例：

| 文件 | 内容 | 映射到 | 保留 | 状态 |
|---|---|---|---|---|
| `meta.json` | agent、单元、prompt/env 版本、模型、耗时、退出码、token、成本、工具调用统计 | **Run** 这一行 | 永久 | 🟡 版本字段拆成三个且有漏项 → 换成一个 `variant` |
| `context.json` | 白名单、单元、已知词、已有节点、设置 | **Run.input**（冻结的输入数据） | **永久** | 🔴 现在和 45 MB 的事件流放在同一个可清理目录（H8） |
| `brief.md` | 由模板 + 输入渲染出的简报 | Run.input 的渲染结果 | 永久（小，8 KB） | 🟢 |
| `events.jsonl` | pi 的原始事件流 | ① **RunStep**（统一轨迹）：只取 `message_end`、`tool_execution_start/end`，约 30-50 行；② 原文作为可过期附件 | 步骤永久；原文 30 天 | 🔴 一份 45 MB 的文件里 136,347 行是 `message_update`（逐字流式输出），有用的不到 50 行 |
| `submissions.jsonl` | 每次 `submit_plan`：接受 / 退回 + 退回原因 | RunStep（tool = submit_plan，含错误信息） | 永久 | 🟢 这是**运行中自我修正**的数据，很有价值：看 agent 被退回几次、因为什么 |
| `fetches.jsonl` | 抓过的网页：URL、状态、标题、链接 | ① RunStep（tool = fetch_url）；② **学习域的材料登记**（同一 URL 多次运行共享） | 登记永久；正文按抓取时间缓存、可过期 | 🟡 现在每次运行各存一份 |
| `output.json` / `output.md` | 课程计划 / 渲染稿 | **Run.output**（候选产物） | 永久 | 🟢 |
| `stderr.log` | pi 的错误输出 | 附件 | 30 天 | 🟢 |
| `eval.json` → `checks[]` | 确定性检查，每项 ok / detail | **Grade**（grader = `check`，每项一个维度） | 永久 | 🟢 |
| `eval.json` → `judge` | 维度分 + 理由、top_issue、suggestion | **Grade**（grader = `judge`，grader_version = rubric 哈希 + 评分模型 + 评测代码哈希） | 永久 | 🟡 现在不记评分器版本 → 改了 rubric 以后分数不可比 |
| `eval.json` → `suspect_checks` | 评分模型质疑的内容回原文核对的结果 | **Grade**（grader = `claim_check`，引用 judge 那条 Grade） | 永久 | 🟢 |
| `judge_input.md` / `judge_raw.txt` | 评分模型的输入和原始回复 | judge Grade 的附件 | 30 天 | 🟢 |
| `lab_verify.json` | ok、problems、warnings、每节结果 | **Grade**（grader = `practice_verify`，cs-practice 注册） | 永久 | 🟢 |
| `review.md` | 导师审阅：结论、各方面判断、"下一次改 prompt" | ① **Grade**（grader = `review`，actor = agent:tutor）：结论 + 各维度；② "下一次改什么"→ **ChangeProposal 草稿** | 永久 | 🔴 自由文本；8 次运行只有 3 份 |
| `learner_eval.json` | 课程评测：每节预测 vs 实际 | **Grade**（grader = `outcome`，`refs[]` = 引用的学习证据 id） | 永久 | 🟡 现在写在运行目录里，可能被清理 |

其它位置的：

| 现在 | 映射到 | 状态 |
|---|---|---|
| `agents/<name>/agent.yaml` + `SYSTEM.md` + `task.md` | **AgentSpec**（稳定身份）+ **Variant** 的组成部分 | 🟡 |
| `agents/<name>/evals/cases.yaml` | **Dataset** 的定义（进 git）；每条 case 要补冻结输入 | 🔴 没有冻结输入（H3），没有开发集 / 留出集之分（H4） |
| `agents/<name>/evals/rubric.md` | judge 评分器版本的组成部分 | 🟡 |
| `agents/<name>/evals/results.jsonl` | **不再存**：它是 Run × Grade 的汇总视图，改成查询 | 🟢 |
| `preps/<unit>.json` 的 `provenance` | **Publication**（哪次 Run 的产出、何时、谁发布成了哪个单元的当前版本） | 🟡 |
| `preps/history/<unit>/<run>.json` | **ArtifactVersion**（内容不可变） | 🟢 |

### 3.1 Variant 的组成（整体一个哈希）

| 组成 | 现在的来源 | 现在算进版本了吗 |
|---|---|---|
| 岗位说明（core 片段 + spec 片段） | `SYSTEM.md` | ✅ prompt_version |
| 任务模板 | `task.md` | ✅ prompt_version |
| **上下文配方** | `runner.build_brief`、`knowledge_context`、`format_knowledge` 的代码 | ❌ |
| 模型 + 参数 | `agent.yaml` 的 `model` | ⚠️ 模型 id 单独记了，thinking 等参数没记 |
| 工具集 | `agent.yaml` 的 `tools` | ❌ |
| 工具实现 | `agent_env/tools.py` | ✅ env_version |
| 学科 spec 的校验规则 | 现在混在 `tools.py` 里 | ✅（间接） |
| 运行时 | pi 的版本 + `env_bridge.ts` | ⚠️ bridge 算了，pi 版本没算 |

新做法：`variant_id = hash(各组成的内容哈希)`，同时把各组成的哈希分别存下来。这样既能说"这是同一个 Variant"，也能说"这两个 Variant 只差在上下文配方"。

评分器也有同样的版本问题：`grader_version = hash(rubric + 评分模型 id + 评测代码)`。

---

## 4. 逻辑表结构

只列逻辑结构和约束，不写 DDL（DDL 在 backend-core-design §5 按这里重写）。**加粗**的是本表的约束或最重要的列。

### 4.1 学习域

```
learner            id PK · created_at
                   （画像内容在文件里：learner/profile.yaml、learner/spec/<spec>.yaml）

evidence           id PK · ts · learner_id FK · schema_version
                   actor_type · actor_id · actor_role · actor_variant
                   verb · spec（core 或学科 spec 名）
                   object_type · object_id · object_version
                   unit_id · plan_id · section · caused_by FK(evidence) · run_id FK(run)
                   score · ok · pending
                   payload_json
                   ▸ 只追加：触发器拒绝 UPDATE / DELETE
                   ▸ CHECK (pending = 1) ⇔ (score IS NULL)
                   ▸ verb 必须在 verb 注册表里（应用层校验 payload 形状）

evidence_node      evidence_id FK · node_id · PK(evidence_id, node_id)
                   ▸ 证据影响哪些知识节点；"按节点查证据"走这张表的索引

artifact_version   id PK（= 产出它的 run id，或人工发布时的 id）· kind（course_plan / explainer_doc / …）
                   unit_id · schema_version · body_json · content_hash · created_at
                   ▸ 不可变

publication        id PK · ts · unit_id · kind · artifact_version_id FK · published_by（actor）
                   ▸ 只追加；"某单元的当前版本" = 最新一条 publication（投影，不存指针）

material           id PK · url UNIQUE · title · kind（page / video / pdf）· first_seen
material_fetch     id PK · material_id FK · ts · status · final_url · blob_id FK · run_id FK
                   ▸ 抓取正文是附件（可过期）；登记永久

practice_snapshot  learner_id · unit_id · plan_id · section · spec · path · created_at
                   PK(learner_id, unit_id, plan_id, section)
                   ▸ cs-practice 的练习场快照登记；文件在磁盘上，不可重建
```

**不需要的表**（都是投影，现场算）：掌握度、节点状态、薄弱点、学习会话、单元进度、已知词表、当前有效的策略、progress.md。

**为什么证据只用一张表**：

| | 一张 `evidence` + `payload_json` | 每种 verb 一张表 |
|---|---|---|
| 新功能加 verb | 注册一个 verb，不改表 | 加表、加迁移 |
| 投影要跨种类读（时长、节点状态都要读所有证据） | 一次查询 | UNION 十几张表 |
| 类型安全 | 应用层按 (verb, schema_version) 校验 payload | 数据库列类型 |
| 结论 | ✅ 选这个；常查的字段（unit、plan、section、score）已经提成列 | |

### 4.2 harness 域

```
variant            id PK（整体哈希）· agent · components_json（各组成的哈希 + 模型参数）· first_seen
                   ▸ 内容来自 git 里的文件，这张表只登记"出现过哪些版本"

run                id PK · agent · variant_id FK · mode（batch / interactive）
                   learner_id FK · unit_id · case_id（评测运行才有）· experiment_id · repeat（第几次）
                   started · seconds · exit_code · status（ok / error / timeout）
                   tokens · cost_usd
                   input_json（冻结的输入数据）· input_rendered_blob（brief）
                   output_json · output_blob
                   raw_trace_blob（可过期）
                   ▸ 只追加；run 结束后不改

run_step           run_id FK · idx · ts · kind（model / tool_call / tool_result / error）
                   tool · ok · tokens · cost · summary · detail_json
                   PK(run_id, idx)

grade              id PK · ts · run_id FK · grader（check / judge / claim_check / practice_verify / review / outcome）
                   grader_version · actor（谁打的分）
                   score · verdict（publish / revise / reject，可空）· dims_json（维度 → 分 + 理由）
                   issues_json（[{layer, where, what}]）· refs_json（引用的 run_step / evidence / 另一条 grade）
                   blob_id（judge 原文等附件）
                   ▸ 只追加；重新评测 = 新的一条

dataset_case       id PK · agent · split（dev / holdout）· unit_id · input_json（冻结输入）
                   expect_json（must_mention / must_not_mention / checks）· version
                   ▸ 定义在 git（cases.yaml）；表里是登记，用来引用

experiment         id PK · agent · variants[] · dataset_version · repeats（默认 3）
                   status · conclusion · started · finished

change_proposal    id PK · ts · agent · layer（prompt / context / tools / model / runtime）
                   trigger_grades[] · hypothesis · from_variant · to_variant
                   experiment_id · status（draft / testing / adopted / rolled_back）· decided_by
                   ▸ 状态变化追加成事件，或者整条只追加新版本（二选一，实现时定）

eval_change        id PK · ts · agent · target（case / rubric / grader）· what · why
                   proposed_by · approved_by（必须是 learner）· status
                   ▸ 优化者不能改评测：没有 learner 批准，新的评分器版本不能用于采纳决定

blob               id PK（内容 sha256）· path · bytes · kind · created_at · expires_at
                   ▸ 大文件在磁盘（runs/blobs/），表里只有索引；expires_at 到期由清理命令删文件
```

**连接两个领域的外键**：
- `grade.refs_json` 里的 evidence id（outcome 评分引用学习证据）
- `evidence.run_id`（讲解 agent 的 `replied`、tutor 的 `observed` 是哪次运行产生的）
- `publication.artifact_version_id` → `artifact_version.id` = 产出它的 `run.id`

### 4.3 查询 → 索引（由现有功能反推）

| 查询 | 谁要 | 索引 |
|---|---|---|
| 某学习者某节点的全部证据（节点状态、薄弱点、`kg show`） | 投影、agent 上下文 | `evidence_node(node_id)` + `evidence(learner_id, ts)` |
| 某单元某版本的学习事件（进度、课程评测） | 课程页、outcome 评分 | `evidence(learner_id, unit_id, plan_id, ts)` |
| 某道题的作答和运行（批改、`study.py runs`） | tutor | `evidence(learner_id, object_type, object_id, ts)` |
| 待批改 | `study.py weak` | `evidence(pending) WHERE pending = 1` 部分索引 |
| 时间范围内的全部证据（学习时长） | `study.py time` | `evidence(learner_id, ts)` |
| 某 Variant 在某数据集上的全部分数 | Experiment | `run(variant_id, case_id)` + `grade(run_id, grader)` |
| 某单元当前发布的是哪次运行 | 课程页 | `publication(unit_id, ts)` |

---

## 5. 留在文件里的（进 git，人审）

| 文件 | 层 | 读取方式 |
|---|---|---|
| `lessons/<topic>/<slug>/quiz.yaml · key.yaml · code/` | 实例（题）；检验器由 spec 提供 | `LessonRepository`，只读；题目版本按内容哈希 |
| `knowledge/<topic>.yaml` | 实例 | `KnowledgeRepository`；并入 agent 提议的节点时写回 YAML |
| `progress/syllabus.yaml` | 实例 | 只读 |
| `curriculum.md` | 实例 | 材料白名单来源 |
| `learner/profile.yaml`、`learner/spec/<spec>.yaml`（由 `learner.md` 拆出） | 实例 | 只读，进 Run.input 快照 |
| `specs/<spec>/`（新：cs-practice 的检验器注册、练习环境、计划校验规则、环境字段 schema、prompt 片段、rubric 附加项） | 学科 spec | 启动时加载 |
| `agents/<name>/`（agent.yaml、prompt、evals/cases.yaml、evals/rubric.md） | core（agent 定义）+ spec 片段 | 启动时加载；内容哈希进 Variant |
| `design/`、`docs/` | — | 人读 |

**数据库导出**：`study.py export` 把 `evidence`、`grade`、`change_proposal`、`eval_change` 导出成 JSONL 进 git，保留"在 diff 里看得见进度和改进历史"。数据库文件本身不进 git（data-requirements §12 已定方向）。

---

## 6. 迁移清单（一次性导入）

| 来源 | 目标 | 规则 | 核对 |
|---|---|---|---|
| `attempts.jsonl` | evidence（answered / graded / logged_practice） | §1.1，保留原 id；§2 ①⑦⑧ | 导入前后 `study.py weak` 逐字段相同 |
| `runs.jsonl` | evidence（ran_tests） | §1.2 | 条数相同 |
| `study_log.jsonl` | evidence | §1.3；`plan` 用现有规则补齐；§2 ②③⑥ | 导入前后每个单元的 `progress` 相同 |
| `preps/*.json` + `history/` | artifact_version + publication | provenance → publication | 当前版本相同 |
| `.sandbox/labs/**/s*` | practice_snapshot | 按目录登记 | 条数 = 目录数 |
| `runs/agents/**` | run、run_step、grade、blob、material、material_fetch | §3；`events.jsonl` 转成步骤后原文放进 blob（30 天后过期） | 每次运行的 tokens / cost 和 meta.json 相同 |
| `review.md` ×3 | grade（review）+ change_proposal（draft） | tutor 手工转换 | 你过目 |
| `learner.md` | profile.yaml、spec/cs-practice.yaml、observed、proposed/confirmed_strategy | §2 ⑨，tutor 手工转换 | 你过目 |
| `evals/results.jsonl` | 不导入 | 它能从 run + grade 重算；导入后对比一次，一致即可 | 10 行对得上 |

---

## 7. 实现记录（2026-09-25，和上面设计的差别）

按"个人项目早期、不做兼容、只要最小结构"实现，和 §1-§6 的设计有这些差别：

| 设计 | 实现 | 原因 |
|---|---|---|
| 练习场操作用 cs-practice 的 verb（`ran_command` / `restored_lab` …） | 一个 core verb `practiced`（payload.op = run / restore / reset / fill） | 练习环境的这组操作是 core 的扩展点（`PracticeEnv`），学别的学科也可能有；只有 `lab` 检查点题型和 bash 实现属于 cs-practice |
| 整节通过时把新词塞进检查点作答的 `nodes`（旧做法），§2 ③ 设计成移到投影里 | 系统写一条 `passed_section` 证据（actor = system，nodes = 这一节的新词） | 投影里做需要节点状态知道课程计划；显式事件更简单，也能看出是哪条规则、什么时候给的 |
| `practice_snapshot` 表 | 没有建；快照文件在 `data/labs/`，不再放在看起来像缓存的 `.sandbox/` 里 | 目前只有一个读者（练习场自己），登记表没有查询需求 |
| `material` / `material_fetch` 表 | 没有建；读过的页面（含原文）在运行目录的 `fetches.jsonl` | 还没有跨运行复用材料的功能（讲解 agent 来了再建） |
| `evidence_node` 表 | 建了 | 按节点查证据是节点状态的主查询 |
| `artifact_version` + `publication` | 叫 `plan_version` + `publication` | 目前只有课程计划一种产物 |
| `dataset_case` / `experiment` / `change_proposal` / `eval_change` 表 | 没有建 | 优化者现在是导师，先用 `agent report`（按版本对比、列出差在哪一层）+ 结构化 `review` 覆盖；自动优化 agent 来了再建 |
| `blob` 表 | 没有建；大文件在运行目录的 `raw/`，按目录清理 | 同上，最小实现 |
| 导入的旧运行 | 版本组成如实记成 `legacy_prompt` / `legacy_env` / `model` | 旧版本号本来就漏了上下文配方和工具（H1），不补造 |
