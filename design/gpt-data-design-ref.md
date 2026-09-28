如果你说的是一种 **“面向学习 Agent 的知识系统”**，它同时要支持：

1. 知识点之间有动态 prerequisite
2. 用户对每个知识点有动态 mastery
3. 可以快速搜索某个知识点及其上下游
4. 可以按不同 slice 展示知识状态
5. 可以根据当前状态推理“下一步学什么”

那么我会建议：**不要把它设计成一个传统 Knowledge Graph，而是设计成 `Knowledge Graph + Learner State + Evidence + Projections` 四层数据模型。**

核心原则是：

> **知识本身是相对稳定的；用户对知识的状态是动态的；推荐路径是推理出来的，而不是存死的。**

---

# 1. 最核心的数据结构

可以先抽象成：

```text
Knowledge
   │
   ├── prerequisite edges
   ├── related edges
   ├── hierarchy
   │
   ▼
Learner State
   │
   ├── mastery
   ├── confidence
   ├── evidence
   ├── history
   │
   ▼
Inference
   │
   ├── missing prerequisites
   ├── next concepts
   ├── review concepts
   └── learning path
```

例如：

```text
Object-Oriented Design
        │
        ├── prerequisite → Abstraction
        ├── prerequisite → Encapsulation
        ├── prerequisite → Polymorphism
        │
        ▼
   Low Coupling
        │
        ├── prerequisite → Object Responsibility
        ├── prerequisite → Information Expert
        │
        ▼
   Law of Demeter
```

用户状态则完全是另外一套东西：

```text
User A

Abstraction
  mastery: 0.91

Encapsulation
  mastery: 0.83

Object Responsibility
  mastery: 0.42

Information Expert
  mastery: 0.67

Low Coupling
  mastery: 0.31
```

这样你不会把：

> “Low Coupling 的 prerequisite 是 Object Responsibility”

和：

> “这个用户已经掌握 Object Responsibility”

混在一个 graph 里面。

---

# 2. Knowledge Node 应该长什么样

我会让一个知识点至少有：

```json
{
  "id": "concept:low-coupling",
  "type": "concept",

  "title": "Low Coupling",

  "definition": "...",

  "parent": [
    "concept:object-oriented-design"
  ],

  "prerequisites": [
    {
      "concept_id": "concept:object-responsibility",
      "strength": 0.85,
      "type": "required"
    },
    {
      "concept_id": "concept:information-expert",
      "strength": 0.70,
      "type": "recommended"
    }
  ],

  "related": [
    "concept:high-cohesion",
    "concept:law-of-demeter"
  ],

  "difficulty": 0.62,

  "sources": [
    "cmu:17-214:lecture-03"
  ]
}
```

这里有一个非常重要的设计：

### prerequisite 不要只是一个 array

不要：

```json
"prerequisites": ["A", "B", "C"]
```

而应该是：

```json
{
  "from": "A",
  "to": "B",
  "relationship": "prerequisite",
  "strength": 0.8,
  "confidence": 0.95,
  "source": "expert"
}
```

因为以后你一定会遇到：

> A 到底是不是 B 的 prerequisite？

这不是 binary question。

可能是：

```text
A ──required────→ B
C ──helpful─────→ B
D ──related─────→ B
```

甚至不同课程的 prerequisite graph 都不同。

---

# 3. 真正重要的是 Learner State

我反而认为这是你这个系统最有价值的部分。

不要：

```json
{
  "concept": "low-coupling",
  "mastery": 0.6
}
```

这么简单。

应该把 **mastery 当成一个随 evidence 更新的 latent state**。

比如：

```json
{
  "user_id": "u123",
  "concept_id": "concept:low-coupling",

  "mastery": 0.63,

  "confidence": 0.71,

  "status": "developing",

  "last_assessed_at": "2026-09-26T20:10:00Z",

  "evidence": {
    "attempts": 5,
    "correct": 3,
    "second_attempt_success": 2
  },

  "history": [
    {
      "timestamp": "...",
      "mastery": 0.31,
      "source": "diagnostic"
    },
    {
      "timestamp": "...",
      "mastery": 0.52,
      "source": "practice"
    },
    {
      "timestamp": "...",
      "mastery": 0.63,
      "source": "retry"
    }
  ]
}
```

这里我会特别强调：

> **不要把 mastery 当成事实，而是当成模型对用户掌握程度的当前估计。**

---

# 4. Evidence 比 mastery 更重要

因为：

```text
mastery = inference
evidence = observation
```

比如用户：

> 第一次回答：错
> AI feedback
> 第二次回答：对

你真正记录的是：

```json
{
  "attempt_id": "attempt_891",

  "concepts": [
    "low-coupling",
    "information-expert"
  ],

  "attempt": "...",

  "evaluation": {
    "correctness": 0.42,
    "reasoning": 0.31
  },

  "feedback": "...",

  "retry": {
    "correctness": 0.87,
    "reasoning": 0.79
  }
}
```

然后：

```text
Evidence
   ↓
Mastery estimation
   ↓
Next-step inference
```

而不是：

```text
User answers correctly
   ↓
mastery = 1
```

---

# 5. 动态 prerequisite 应该怎么处理

这是你问题里最难、也最有意思的一部分。

我不会让：

```text
Concept
  prerequisites = [...]
```

成为唯一真相。

而是：

```text
Canonical Knowledge Graph
+
Contextual prerequisite graph
```

例如：

```text
Low Coupling
```

在不同 context：

### CS course

```text
Object
  ↓
Responsibility
  ↓
Information Expert
  ↓
Low Coupling
```

### Software engineering interview

可能变成：

```text
SOLID
  ↓
Single Responsibility
  ↓
Low Coupling
```

### User-specific

如果用户已经非常熟悉 OOP：

```text
User mastery

OOP          0.95
Responsibility 0.91
Information Expert 0.88
Low Coupling 0.32
```

那么实际学习路径：

```text
Low Coupling
```

而不是重新走：

```text
OOP → Responsibility → Information Expert → Low Coupling
```

所以：

> **prerequisite graph 是 knowledge-level structure，而 learning path 是 knowledge graph × learner state 的结果。**

这两个东西不要混。

---

# 6. 推荐的数据模型其实可以是三张核心表

如果你最后落到 Postgres，我甚至不会一开始搞 Neo4j。

### `concepts`

```text
id
title
description
parent_id
difficulty
metadata
created_at
updated_at
```

### `concept_edges`

```text
source_id
target_id

edge_type
    prerequisite
    related
    example_of
    contrasts_with

strength
confidence

source
version

created_at
```

### `learner_concept_state`

```text
user_id
concept_id

mastery
confidence

attempt_count
success_count

last_attempt_at
last_mastery_update

state_version
```

然后：

### `evidence`

```text
id
user_id
concept_id
attempt_id

evidence_type
    answer
    explanation
    question
    correction
    retry
    observation

raw_content
score

created_at
```

这套结构已经能支撑非常多东西。

---

# 7. “不同切片”不要复制知识图

你提到的：

> 不同切片，用于展示用户当前知识点掌握情况

这个我会特别建议不要做成不同 graph。

而是：

> **同一 Knowledge Graph，不同 Projection。**

例如同一份数据可以 projection 成：

### Topic slice

```text
Object-Oriented Design

████████ 90% Abstraction
███████  82% Encapsulation
████      48% Responsibility
███       31% Low Coupling
```

### Dependency slice

```text
Low Coupling
     ↑
Information Expert
     ↑
Object Responsibility
     ↑
Object-Oriented Design
```

### Weakness slice

```text
Lowest mastery

1. Low Coupling       31%
2. Law of Demeter     39%
3. Responsibility     48%
```

### Learning trajectory

```text
Day 1    31%
Day 3    45%
Day 5    58%
Day 7    63%
```

### Course slice

```text
CMU 17-214

Lecture 1
  ├── Objects       92%
  ├── Responsibility 88%

Lecture 2
  ├── Coupling      31%
  └── Cohesion      52%
```

**都不需要改变底层知识模型。**

---

# 8. Search 层也应该独立

你说：

> “能实现快速搜索”

我会做：

```text
Postgres
    │
    ├── exact search
    ├── full-text search
    │
    ▼
Vector Index
    │
    ▼
Semantic Search
```

搜索：

> “Law of Demeter”

可以找到：

```text
Law of Demeter
Low Coupling
Tell Don't Ask
Object Responsibility
```

但搜索结果最好不是单纯返回 concept。

而是：

```json
{
  "concept": "Law of Demeter",

  "mastery": 0.42,

  "prerequisites": [
    {
      "concept": "Object Responsibility",
      "mastery": 0.91
    },
    {
      "concept": "Low Coupling",
      "mastery": 0.31
    }
  ],

  "next_concepts": [
    "Message Passing",
    "Encapsulation"
  ]
}
```

也就是说：

> **Search 应该返回 Knowledge + User State 的联合视图。**

这对你的产品会非常重要。

---

# 9. 最后是推理层

真正的：

> “接下来应该学什么？”

不是数据库里的一个字段。

而应该是一个 function：

```text
next_concepts(
    user,
    goal,
    current_concept,
    knowledge_graph,
    learner_state
)
```

例如：

```text
Goal:
Master Object-Oriented Design

Current:
Low Coupling

Knowledge Graph:

Responsibility ─────┐
                    ↓
Information Expert → Low Coupling
                    ↓
Law of Demeter
```

用户：

```text
Responsibility     0.92
Information Expert 0.81
Low Coupling       0.35
Law of Demeter     unknown
```

系统推理：

```text
Low Coupling
     ↑
Information Expert = 0.81
Responsibility = 0.92

→ prerequisites sufficiently mastered

→ Low Coupling is ready
```

所以：

```json
{
  "next": "low-coupling",
  "reason": {
    "goal_relevance": 0.92,
    "prerequisite_readiness": 0.87,
    "mastery_gap": 0.65
  }
}
```

这比数据库直接存：

```json
"next": "low-coupling"
```

强很多。

---

# 10. 我会把整个系统设计成这个样子

```text
                    KNOWLEDGE LAYER
              ┌────────────────────────┐
              │ Concepts               │
              │ Definitions            │
              │ Prerequisites          │
              │ Relationships          │
              │ Sources                │
              └───────────┬────────────┘
                          │
                          ▼
                    LEARNER LAYER
              ┌────────────────────────┐
              │ Mastery                │
              │ Confidence             │
              │ Attempts               │
              │ Learning History        │
              │ Evidence               │
              └───────────┬────────────┘
                          │
                          ▼
                  INFERENCE LAYER
              ┌────────────────────────┐
              │ Readiness              │
              │ Knowledge Gaps         │
              │ Next Concept           │
              │ Review Need            │
              │ Learning Path          │
              └───────────┬────────────┘
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          SEARCH       DASHBOARD      AGENT
             │            │            │
        "what is X?"   "where am I?"  "what next?"
```

---

## 一个我认为特别关键的 architectural decision

如果这是你现在 H.Education 在考虑的东西，我甚至会把它进一步压缩成：

> **Knowledge Graph 不是产品。Learner State 才是产品。**

知识图谱负责回答：

> **世界上这些知识之间是什么关系？**

Learner State 负责回答：

> **这个人现在在哪里？**

Inference 负责回答：

> **从这里到目标，下一步最值得做什么？**

而 Evidence 负责回答：

> **你凭什么认为他在这里？**

这四个问题分开之后，系统会非常干净。

尤其你前面一直在想的 **Goal → Attempt → Feedback → Retry**，其实正好可以成为这个知识系统的 **state update mechanism**：

```text
             Attempt
                ↓
             Evidence
                ↓
        update Learner State
                ↓
       recompute readiness
                ↓
         choose next node
                ↓
             Attempt
                ↓
              ...
```

这样它就不是一个“把教材切成知识点 + 给每个知识点打分”的 Knowledge Graph，而会更接近一个 **可持续更新的 learner model + knowledge model**。这可能才是你真正需要的系统。
