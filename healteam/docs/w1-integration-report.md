# W1 集成验证报告（**修订版**）

> 路径：`healteam/docs/w1-integration-report.md`
> 报告日期：2026-06-03
> 修订日期：2026-06-03（v1.1，纠正"骨架当行为报"的偏差）
> **2026-06-04（v1.2，追加"5+1 Agent 模型"说明，见 §0.5）**
> W1 范围：**架构骨架 + 内容资产**，不包含行为

---

## 0.5 v1.2 追加说明（2026-06-04，W1 收尾后澄清）

> 本节为 v1.2 增补，不修改 v1.0/v1.1 历史。详情见 [`agent-model.md`](./agent-model.md)、[`workflow.md`](./workflow.md)、[`optimization-roadmap.md`](./optimization-roadmap.md)。

**5+1 Agent 模型澄清**：
- W1 文档 v1.1 写"5 Agent"和"5 个 agent 协作"——这是**治疗室视角**的正确数字
- 写"5 Agent prompt（6 文件 + 编排器）"——已暗示治疗室 5 个 + 心理医生 1 个
- v1.2 统一明确：**6 个 Agent = 5 个治疗室 + 1 个督导会专属**
  - 治疗室（5）: `doctor` 主治医师（发言）+ `observer` 旁观者（旁路 whisper）+ `father` / `mother` / `patient` 家庭（发言）
  - 督导会专属（1）: `psychiatrist` 心理医生——**不在治疗室发言**
- v1.0/v1.1 的"5 Agent"是从治疗室视角看,与"6 个 Agent"是从团队总规模看,**两者一致**——本节消除表述歧义
- **W1 代码契约已正确**: `apps/api/app/routers/agents.py` 的 `speaks_in_session` 字段已正确编码(`psychiatrist=False, observer=False`); 集成测试 `test_psychiatrist_silent_during_session` 已强制

**对 W2 任务的影响**：
- 督导会子图 `packages/agents/supervision.py` 显式构造 `(doctor, psychiatrist, observer)` 3 Agent 上下文,**不挂家庭节点**
- 心理医生 system prompt 明确"你不参与治疗室发言"

---

---

## 0. 修订说明（重要，先看）

第一次报告（v1.0）把"W1 跑通 20 轮治疗"作为核心成果。这是不准确的。

**修订后的 W1 真实范围**：

- ✅ **架构骨架**：LangGraph 状态机 / Pydantic schema / FastAPI 路由 / Docker Compose 配置 / pytest 测试套 —— **可运行**
- ✅ **内容资产**：5 Agent prompt / 3 流派模板 / 1 案例 JSON / 2 份对比文档 —— **是文档**（运行时无 LLM 消费）
- ❌ **行为**：家庭参与 / 真实 LLM / 旁观者旁路提示 / 督导会 / 里程碑验证 —— **W2 才做**

W1 收尾交付物 = "**内容齐 + 接口通 + demo 不崩**"；不是"**可演示给真实学员用的治疗仿真**"。

修订动机：测试套件里 `test_w1_placeholder_doctor_monopolizes` 把 bug 写成了 spec。第一次报告据此称"doctor 主导是 W1 设计约束"，实际是 `decide_speaker` + `should_speak` 一起决定 W1 跑不出真实家庭对话。已在 v1.1 重命名并改写为诚实描述。

---

## 1. 真实交付物清单

| 类别 | 数量 | 状态 |
|---|---|---|
| `healteam/` 目录文件 | ~90 个 | 落盘 |
| pytest 测试 | 77 个，**全部通过**（0.32s） | 通过 |
| `docs/architecture.md`（架构契约） | 1 份 | 锁定 |
| `data/cases/teen-truancy-01.json` | 1 份 | 落盘 |
| `data/schools/{structural,bowen,satir}.md` | 3 份 | 落盘 |
| `packages/prompts/*.md` | 7 份 | 落盘 |
| `docs/schools-comparison.md` | 1 份 | 落盘 |
| `data/case-school-matrix.md` | 1 份 | 落盘 |
| `scripts/smoke.py` / `scripts/demo_w1.py` | 2 份 | 跑通（不崩） |
| 真实可演示的"家庭治疗"仿真 | **0 份** | W2 |

---

## 2. W1 范围内 PASS 的事项

### 2.1 架构骨架（PASS —— 这才是 W1 的核心交付物）

| 检查 | 结果 | 证据 |
|---|---|---|
| 6 节点 LangGraph 状态机编译通过 | ✅ | `scripts/smoke.py` 跑通 |
| 跑 20 轮不崩、不死循环 | ✅ | `tests/integration/test_session_e2e.py` |
| 状态机按 `check_end` 触发 ENDED | ✅ | `test_orchestrator_runs_to_completion` |
| Pydantic schema 校验合法 | ✅ | `tests/cases/test_case_content.py` 31 个 |
| FastAPI 启动 10 路由 | ✅ | `verify_fastapi.py` 之前测试已验 |
| Docker Compose 配置合法 | ✅ | `docker compose config` |
| pytest 套件 77 个全过 | ✅ | `py -m pytest healteam/tests` |

### 2.2 内容资产（PASS —— 文件落盘且结构完整）

| 资产 | 校验 | 通过 |
|---|---|---|
| 5 Agent prompt（6 文件 + 编排器） | pytest 内容断言 | 36/36 |
| 3 流派模板 | 章节、术语、引用、反向互斥 | 12/12 |
| 1 案例 JSON | schema 字段、防御模式、动力关键词 | 10/10 |
| 3 流派对比文档 | 维度表、决策点、风险表 | （人工审） |
| 案例-流派映射 | 适合度矩阵、关键技能、预期效果 | （人工审） |

**重要**：内容资产**只是文件**。W1 没有任何运行时路径消费它们（除测试代码直接 read）。W2 接 LLM 后才会从这些文件加载 system prompt。

---

## 3. W1 范围内 OUT-OF-SCOPE（明确不做）

| 项 | 状态 | 在哪做 |
|---|---|---|
| 真实 LLM 调用 | ❌ W1 故意 mock | W2 |
| 家庭成员真正参与（轮流发言） | ❌ W1 placeholder doctor 占主导 | W2（接 LLM 后自然修复） |
| 旁观者旁路提示（observer.observe() 返回 Hint） | ❌ W1 永远返回 None | W2 |
| 督导会（doctor + psychiatrist + observer 三方对话） | ❌ W1 完全没实现 | W2 |
| 里程碑实际达成验证 | ❌ W1 不检查 | W2 |
| 案例 JSON `agent_role_hints` 字段使用 | ❌ W1 不读 | W2 |
| Web UI 真实交互 | ❌ W1 只搭骨架 | v0.2 |
| Postgres 持久化 | ❌ W1 in-memory | v0.2 |
| 用户账号 / 班级管理 | ❌ W1 无 | v0.3 |

---

## 4. 关于 demo 的诚实描述

`python healteam/scripts/demo_w1.py` 的实际输出：

```
Round  0 | [doctor] | [doctor placeholder | round=0]
Round  1 | [doctor] | [doctor placeholder | round=0]
...（20 轮都是 doctor 一个人说）
```

**这不是 bug 演示，是 W1 真实行为**。

原因链（不要掩盖）：
1. W1 故意没接 LLM
2. 5 个 Agent 全部返回 placeholder utterance
3. `decide_speaker` 优先级 + `DoctorAgent.should_speak` 一起决定每轮都选 doctor
4. 家庭成员 `should_speak` 依赖"上一条话含关键词"——但 placeholder 永远不含

**W2 接 LLM 后的预期行为**：
- doctor / father / mother / patient 按 case 动力真实轮替
- observer 在家庭陷入僵局时通过 hint 通道给治疗师旁路提示
- psychiatrist 在治疗后的督导会里基于 prompt 给理论支撑
- 里程碑在治疗中被实际检查

---

## 5. 与 PRD 的对应关系（修正）

| PRD 章节 | 第一次报告 | 修订版 |
|---|---|---|
| §6.1 五角色 + 边界 | ✅ | ✅（**内容**层面；**行为**待 W2） |
| §6.2 旁观者旁路 | ✅ | ⏳ 内容已写，**运行时 W2 启用** |
| §6.3 心理医生督导会 | ⏳ 部分 | ⏳ 仅 session 跑通，**督导会代码 W2** |
| §7.1 案例 JSON | ✅ | ✅（**内容**层面；运行时未消费） |
| §8 MVP 范围 | ✅ | ⏳ 真实 MVP 在 W2 |
| §12 风险缓解 | ✅ | ✅ |

---

## 6. 修订版成功指标（不是过度承诺）

| 指标 | 目标 | 实际 |
|---|---|---|
| 77 个 pytest 通过 | 100% | ✅ 100%（0.32s） |
| 架构骨架可启动 | 是 | ✅ |
| 内容资产完整 | 是 | ✅ |
| 演示脚本不崩 | 是 | ✅ |
| 真实家庭治疗仿真 | 不在 W1 | n/a（W2 任务） |

---

## 7. W2 接入路径（具体到第一行代码）

W2 改动的 4 个文件，按依赖顺序：

1. **`packages/agents/doctor.py` 51 行起**：把 `raise NotImplementedError` 改成 `self.llm.chat(messages)`
2. **`packages/agents/orchestrator.py` 99-104 行**：把 `load_case` 改成读 `data/cases/teen-truancy-01.json`
3. **`packages/agents/observer.py` 38-41 行**：把 `return None` 改成基于 LLM 的 hint 检测
4. **新增 `packages/agents/supervision.py`**：督导会子图（doctor + psychiatrist + observer 三方对话）

W2 估时：1.5~2 周（含端到端真实演练 + 人类专家校准循环 1 轮）。

---

*版本：v1.1（修订版）· 修订动机：把"W1 跑通 20 轮治疗"修正为"W1 跑通 20 轮状态机"*
