# HealTeam 工作流（两阶段）

> 路径：`healteam/docs/workflow.md`
> 状态：v1.0 · 与 PRD §4.1 / §9.1 / agent-model.md 同源
> 目的：把"治疗室 + 督导会"两阶段串成端到端可执行流

---

## 0. 一图概览

```
┌──────────────────────────── 学员 ────────────────────────────┐
│                                                              │
│   1. 选案例       2. 选流派       3. 开场  ……  N. 收报告      │
└──┬───────────────┬──────────────┬──────────────────┬──────────┘
   │               │              │                  │
   ▼               ▼              ▼                  ▲
 ════════════ 治疗室阶段  ════════════  ═════════ 督导会阶段 ═════════
  doctor / observer / 3 family        doctor / psychiatrist / observer
   (5 in-session Agents)              (3 agents; family 冻结)
```

---

## 1. 治疗室阶段（Phase 1: Live Therapy）

### 1.1 参与者（5 个）

| Agent | 角色 | 通道 |
|---|---|---|
| `doctor` | 主治医师 | **主对话**（学员 + LLM 共写） |
| `observer` | 旁观者 | **旁路 whisper**（只 doctor 看到） |
| `father` | 父亲 | **主对话** |
| `mother` | 母亲 | **主对话** |
| `patient` | 病人 | **主对话** |

`psychiatrist` **不参与本阶段**——`should_speak` 永远 False。

### 1.2 拓扑（LangGraph StateGraph，6 节点）

```
load_case → decide_speaker → agent_respond → observer_hint → update_memory → check_end
                                                                          ↓
                                                                  (round<20) decide_speaker
                                                                  (round≥20) END
```

定义见 [`packages/agents/orchestrator.py:259-279`](../packages/agents/orchestrator.py)。

### 1.3 数据流（每轮）

```
┌──────────────────────────────────────────────────────────────────────┐
│  round N                                                             │
│                                                                      │
│  1. decide_speaker                                                   │
│     └─ 对 SPEAKING_ACTORS={doctor, father, mother, patient}          │
│        各调 should_speak(ctx) → 取最高优先级                         │
│                                                                      │
│  2. agent_respond                                                    │
│     └─ pending_speaker 调 .respond(ctx) → 写 Event(UTTERANCE)        │
│                                                                      │
│  3. observer_hint  (旁路)                                            │
│     └─ observer.observe(ctx) → 写 Event(HINT)                        │
│        (旁路,不进入主对话流)                                          │
│                                                                      │
│  4. update_memory                                                    │
│     └─ round += 1                                                    │
│                                                                      │
│  5. check_end                                                        │
│     └─ round >= max_rounds? → is_terminated=True                     │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.4 结束条件

- `round >= max_rounds`（默认 20） → `SessionStatus.ENDED`
- 里程碑全部达成（可选，W2 实现）
- 学员显式终止（v0.2 UI 引入）

### 1.5 治疗室输出

- `Session.events`: `Event(UTTERANCE)` + `Event(HINT)` 流
- `Session.interventions`: 学员显式标记的干预（v0.2 UI 引入）
- `Session.emotions` / `Session.relationships`: W2 由 `update_memory` 节点写入

---

## 2. 督导会阶段（Phase 2: Supervision Meeting）

### 2.1 参与者（3 个，独立子图）

| Agent | 角色 | 来源 |
|---|---|---|
| `doctor` | 反思自己的治疗节律 | 治疗室记忆 |
| `observer` | 汇报关键时刻、模式识别 | 治疗室记忆 |
| `psychiatrist` | **首次登场**——理论支撑、引用流派 | 案例 + 流派模板 |

`father` / `mother` / `patient` **冻结**——不挂入子图。

### 2.2 触发

- 治疗室 `SessionStatus=ENDED` 自动触发
- 或学员在 v0.2 UI 点"开始督导会"手动触发

### 2.3 拓扑（W2 待实现 `packages/agents/supervision.py`）

```
doctor_summarize → observer_report → psychiatrist_evaluate → consensus → END
   (1 轮)         (1 轮)            (1-2 轮)                 (1 轮)
                                              ↑___________________│
                                                  (互相回应 1-2 轮)
```

3~5 轮三轮+收尾的对话流。

### 2.4 输出

- **督导共识** = 三方对"成功点 / 失败点 / 改进建议"的共识摘要
- 写入"团队治疗经验库"（v0.2+，影响下一次治疗的提示策略）
- 触发 `SessionReport` 生成

---

## 3. 复盘报告生成（Phase 3: Report）

督导会 ENDED 后：

```
Session 完整记忆 + 督导共识
   ↓
[report_service.generate(session_id)]
   ↓
ReportSummary {
   session_id, generated_at, school,
   summary_md,                ← 督导会共识的人类可读版
   milestones_achieved,       ← 案例 expected_milestones 的达成情况
   interventions_used,        ← 干预技术使用统计
   school_fit_score,          ← 心理医生评分
   key_moments_replay,        ← 关键时刻回放
   next_session_hints,        ← 给下一次演练的建议
}
```

详见 [`apps/api/app/routers/reports.py`](../apps/api/app/routers/reports.py)（W1 返 501，W2 实现）。

---

## 4. 端到端时序

```
学员                    系统                       LLM 客户端
 │                       │                              │
 │ POST /sessions ────►  │ 加载 case + school            │
 │                       │ 写 Session(pending)           │
 │                       │ 启动 LangGraph                │
 │                       │  ──── 每轮 ────                │
 │ ◄─── WS: round N      │  decide_speaker               │
 │     (doctor utterance)│  agent.respond ─────────────► │ 调 LLM (doctor)
 │                       │  observer.observe ───────────► │ 调 LLM (observer)
 │ ◄─── WS: hint        │  write Event                  │
 │     (旁路,不返学员?)  │                              │ ← 在 W2 由 UI 控制
 │  ……… 20 轮 ………       │                              │
 │                       │  SessionStatus=ENDED           │
 │                       │  ─── 督导会子图启动 ───        │
 │                       │  doctor_summarize ──────────► │ 调 LLM
 │                       │  observer_report ───────────► │ 调 LLM
 │                       │  psychiatrist_evaluate ─────► │ 调 LLM  ← 心理医生首次登场
 │                       │  consensus ────────────────► │ 调 LLM
 │                       │  写 ReportSummary              │
 │ ◄─── WS: report     │                              │
 │ GET /reports/{id}    │                              │
```

---

## 5. 与 PRD §9.1 数据流的对应

PRD §9.1 的单次治疗完整数据流（修订后）：

```
[学员选择案例+流派]
       ↓
[加载家庭背景卡 → 写入共享记忆]
       ↓
[循环 1~20 轮]              ← 治疗室阶段（Phase 1）
  ├── 编排器决定发言人
  ├── 对应 Agent 生成回复     ← doctor / father / mother / patient
  ├── 旁观者 Agent 监听 → 触发提示   ← observer (旁路)
  ├── 写入共享记忆
  └── 学员输入（可能）
       ↓
[治疗结束判定]
       ↓
[触发督导会]                ← 督导会阶段（Phase 2）
  ├── 3 Agent 对话          ← doctor / psychiatrist / observer
  ├── 输出督导共识
  └── 写入经验库
       ↓
[生成复盘报告]              ← 报告阶段（Phase 3）
       ↓
[更新旁观者规则库权重]
       ↓
[返回报告给学员]
```

每个 `[方括号]` = 本工作流的一个 Phase。

---

## 6. W1 / W2 状态一览

| Phase | W1 | W2 |
|---|---|---|
| 1. 治疗室 StateGraph | ✅ 6 节点编译跑通,placeholder utterance | 接 LLM,case JSON 注入,observer 真实 hint |
| 2. 督导会子图 | ❌ 不存在 | 新增 `packages/agents/supervision.py`,3~5 轮对话 |
| 3. 报告生成 | ❌ 返 501 | 督导共识 → ReportSummary |
| 4. 经验库更新 | ❌ 规则库空 | 写入 + 权重更新(L1 进化) |
| 5. WebSocket 流式 | ❌ 无 UI | 治疗室+督导会全程推送 |

---

*版本：v1.0 · 与 PRD §6.0 (5+1 模型) 一致。任何代码修改应同时更新本文件和 agent-model.md。*
