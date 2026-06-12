# HealTeam Agent 模型（5+1 分类）

> 路径：`healteam/docs/agent-model.md`
> 状态：v1.0 · 锁定（PRD §6.0 同源）
> 来源：2026-06-04 PRD 修订决定

---

## 0. 一句话

> **6 个 Agent = 5 个治疗室 + 1 个督导会专属**。心理医生是第 6 个 Agent，**不参与治疗室**，**只参与督导会**。

## 1. 分类表

| 分类 | 数量 | Agent | 治疗室发言 | 治疗室旁路 | 督导会发言 | 角色代号 |
|---|---|---|---|---|---|---|
| **治疗师** | 1 | `doctor`（AI 主治医师） | ✅ | n/a | ✅ | A1 |
| **旁观者** | 1 | `observer`（AI 旁观者） | ❌ | ✅ whisper | ✅ | A3 |
| **家庭成员** | 3 | `father` / `mother` / `patient` | ✅ | n/a | ❌ | A4-A6 |
| **理论顾问** | 1 | `psychiatrist`（AI 心理医生） | ❌ | ❌ | ✅ | A2 |

**核心不变量**:
- `psychiatrist.should_speak()` 在治疗室 LangGraph 中**永远 False**——由 `tests/integration/test_session_e2e.py::test_psychiatrist_silent_during_session` 强制。
- 督导会子图**不挂家庭节点**——`father` / `mother` / `patient` 在督导会阶段不发声。
- `observer` 在治疗室通过 `Hint` 旁路通道 whisper，**不写主对话流**；在督导会转为正式发言。

## 2. 与代码契约的对应

| 文档/契约 | 位置 | 当前实现 |
|---|---|---|
| `speaks_in_session` 字段 | `apps/api/app/routers/agents.py` | `doctor=True, observer=False, father=True, mother=True, patient=True, psychiatrist=False` |
| 治疗室 `SPEAKING_ACTORS` | `packages/agents/orchestrator.py` | `(DOCTOR, FATHER, MOTHER, PATIENT)` —— 4 个 speakers |
| `observer.observe()` | `packages/agents/observer.py` | 治疗室返回 `Optional[Hint]`；督导会时调用 `respond()` |
| `psychiatrist.should_speak()` | `packages/agents/psychiatrist.py` | 治疗室应返回 `False`；督导会时由督导子图显式调用 `respond()` |
| 测试 `test_psychiatrist_silent_during_session` | `tests/integration/` | 已通过 |
| 测试 `test_observer_runs_each_round` | `tests/integration/` | 已通过 |

> **W1 状态**: 治疗室模型已编码 + 测试已锁。督导会子图 W2 实现。
> **W2 任务**: 新增 `packages/agents/supervision.py`,实现 `(doctor, psychiatrist, observer)` 三方 LangGraph 子图。

## 3. 角色代号 vs PRD 编号对照

PRD §6.1-6.4 把 6 个 Agent 编号为 Agent 1-6(治疗师 1、医生 2、旁观者 3、家庭 4-6)。本模型用更功能性的代号:

| 本模型 | PRD 编号 | Agent 名 | 备注 |
|---|---|---|---|
| 治疗师 | Agent 1 | `doctor` | PRD 称"主治医师" |
| **理论顾问** | **Agent 2** | **`psychiatrist`** | **本模型明确: 只在督导会** |
| 旁观者 | Agent 3 | `observer` | PRD 称"旁观者 / 进化引擎核心" |
| 家庭 A | Agent 4 | `father` | |
| 家庭 B | Agent 5 | `mother` | |
| 家庭 C | Agent 6 | `patient` | |

## 4. 为什么这样分?(设计理由)

| 决策 | 理由 |
|---|---|
| 心理医生**不**参与治疗室 | 治疗室需要保真"家庭 + 一位治疗师"的动力学；过早引入理论视角会破坏"家庭系统被学员直接观察"的学习价值。 |
| 心理医生**是**第 6 个独立 Agent(不是 doctor 的子模块) | (a) 督导会需要 3 方独立判断(医生反思 / 旁观者观察 / 心理医生理论); (b) 心理医生的 prompt 与 doctor 完全不同,共享实例会污染 LLM context。 |
| observer 在治疗室只 whisper | observer 的输出是"给医生的耳麦",**不能**污染主对话流(学员侧会看到 observer 的话会破坏"我是医生"的代入感)。 |
| 家庭在督导会不参与 | 督导会的目的是"医生被反馈",不是"家庭再次被讨论"。家庭若参与,会变成 6 方对话,理论支撑的密度被稀释。 |

## 5. 反例(明确不做的)

- ❌ "心理医生在治疗室偶尔给医生一句理论提醒"——破坏学习价值。
- ❌ "observer 在治疗室也作为 speaker 发问"——破坏旁观者客观性。
- ❌ "家庭在督导会重新出场被三方分析"——督导会是对学员的复盘,不是对家庭的二次治疗。
- ❌ "把 doctor 和 psychiatrist 合并成一个 '治疗师+理论家' Agent"——失去三方独立判断。

---

## 6. 变更历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-06-04 | 首次明确"5+1 模型"——心理医生只在督导会。来源: PRD §6.0 修订 + 本文件。 |

*本文件是 5+1 Agent 模型的**单一事实来源**(Single Source of Truth)。任何代码或文档与此不一致时,以本文件为准,并提 issue 同步。*
