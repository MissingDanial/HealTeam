# HealTeam 后续治疗方向优化路线图（W2+）

> 路径：`healteam/docs/optimization-roadmap.md`
> 状态：v1.0 · 与 PRD §12/§14/§15 同源
> 时间范围：W2（接 LLM）→ v0.2（持久化）→ v0.3（多用户）→ v0.4（专家校准）→ v1.0（机构版）

---

## 0. 优先级总览

| 阶段 | 时间 | 关键里程碑 | 治疗方向影响 |
|---|---|---|---|
| **W2** | 1.5~2 周 | 接真 LLM，督导会跑通 | 治疗仿真从骨架→真实可演练 |
| **v0.2** | 2 周 | Postgres 持久化 + 经验库 | 跨会话学习成为可能 |
| **v0.3** | 4 周 | 案例库扩展到 5+ | 治疗覆盖广度提升 |
| **v0.4** | 持续 | 人类督导师校准 | 评估准确度的"金标准" |
| **v1.0** | 8 周 | 机构版 | 商业化基础 |

---

## 1. W2 必修（治疗仿真的"真实性"）

### 1.1 接 LLM 后的治疗室行为（doctor.py:51）

**当前**: 5 个 Agent 返回 placeholder utterance → doctor 主导 100% 轮次。
**目标**: 5 治疗室 Agent 按 case 动力真实轮替 + 真实家庭动力学。

**实现要点**:
- `DoctorAgent.respond` 调 `self.llm.chat(messages)`,`messages` 由 system prompt + 治疗室记忆构造
- `system` 来自 `packages/prompts/doctor.md` + 案例 JSON `school_affinity.<流派>.key_techniques_to_apply`
- 每轮消耗 ~500 tokens；20 轮 + 督导会 5 轮 ≈ **12.5K tokens / session**

**治疗方向优化**:
- **注入 `agent_role_hints`**: 案例 JSON 给每位家庭成员 3 句"开场草案"。W2 必须把这 3 句作为 few-shot 喂给对应 agent 的 system prompt,而不是只用作参考。
  - 效果预期: 家庭成员开场一致性 > 80%,避免"开 3 轮还在问寒问暖"。
- **注入 `opening_context.session_round_0_scene`**: 物理位置 + 情绪状态作为 LLM context,提升空间感。

### 1.2 旁观者真实 hint（observer.py:38-41）

**当前**: 永远返回 `None`。
**目标**: 基于 LLM 识别"治疗僵局 / 防御升级 / 错失干预点 / 节律偏离",返回结构化 `Hint`。

**触发条件库**（已在 observer.md prompt 中定义,W2 实现）:
- `stagnation_3_rounds`: 连续 3 轮无新信息 → "建议探索沉默"
- `defense_escalation`: 阻抗信号增强 → "识别为合理化 / 否认"
- `missed_intervention`: 学员跳过关键提问 → "刚才没接住母亲的悲伤表达"
- `rhythm_deviation`: 节奏过快/过慢 → "建议放慢"

**治疗方向优化**:
- **Hint 必须可操作**——不是"看起来家庭有些紧张",而是"建议面质父亲的'我同事家孩子'对比"。
- **Hint 走结构化输出**——trigger + content + confidence + related_experience,让医生可以基于 confidence 决定是否采纳。
- **Hint 注入 `Experience.related_experience`**——旁观者把"成功 hint 模式"回写经验库(§3)。

### 1.3 督导会子图（新增 `packages/agents/supervision.py`）

**当前**: 完全不存在。
**目标**: doctor / psychiatrist / observer 三方 3~5 轮对话,产出"督导共识"。

**对话流设计**:
```
[doctor_summarize]  1 轮  - 反思治疗节律(哪里做得好,哪里犹豫)
[observer_report]    1 轮  - 汇报关键时刻 + 模式识别
[psychiatrist_evaluate] 1-2 轮  - 理论解读,引用流派(结构派 Minuchin / Bowen / Satir)
[consensus]          1 轮  - 三方收敛共识
```

**治疗方向优化**:
- **psychiatrist 必须引用具体流派原典**——不是"这是边界问题",而是"Minuchin 1974 *Families and Family Therapy* 区分了 rigid/diffuse boundary,本案例 mother-patient 边界是 diffuse_enmeshed"。
- **共识必须可执行**——"下次再遇到类似僵局,学员可尝试 X 技术" 的形式,而不是空泛评价。
- **共识要 cross-reference 案例 JSON 的 `expected_milestones`**——5 个里程碑达成/未达成,作为结构化输出。

---

## 2. v0.2 持久化与跨会话学习

### 2.1 经验库（L1 + L2 进化的物质基础）

**当前**: `InMemoryStore` + `MemoryStore` Protocol stub。
**目标**: Postgres + pgvector,实现跨案例、跨学员的经验检索。

**治疗方向优化**:
- **L1 规则库**: 每条 hint 规则 `(trigger, content, weight)`,每次治疗后基于"学员后续行动→治疗走向"调整 weight。
- **L2 经验库**: 每条督导共识摘要为 `Experience` 对象,支持按 `(school, condition)` 检索。
- **检索路径**: 治疗中 observer 监听 → 命中 condition → 检索相关 experience → 注入 hint 内容。
- **关键不变量**: **人格设定 / 流派基本规则 / 伦理红线** 永远不被覆盖（PRD §7.3）。

### 2.2 学员成长曲线

**当前**: 每次 session 独立,无累积。
**目标**: 同学员的多次 session 形成成长曲线。

**关键指标**:
- 流派契合度评分（psychiatrist 在督导会输出）
- 干预技术使用多样性（doctor 在督导会输出）
- 关键时刻决策准确率（observer 在督导会输出）
- 学员反刍率（同一错误模式重复出现的频率,O-1 校准后才有意义）

---

## 3. v0.3 案例库扩展

### 3.1 从 1 个到 5+ 案例

**当前**: 只有 `teen-truancy-01.json`。
**目标**: PRD §4.1 列出 3 类案例全部落地（青少年厌学 / 婚姻冲突 / 亲子代际矛盾）,v0.3 扩到 5+。

**治疗方向优化**:
- **每个案例必有**: `expected_milestones` (5 个可观察) + `agent_role_hints` (3 家庭成员各 3 句草案) + `clinical_notes.references` (流派原典引用)。
- **新案例入审流程**: 1 位家庭治疗督导师 + 1 位 AI 工程师双签。
- **案例-流派匹配矩阵** (`data/case-school-matrix.md`) 保持更新,避免"流派被错误混合"风险（PRD §14）。

### 3.2 流派的"边界感"防御

**治疗方向优化**:
- 每个流派模板 §2.1 明确"不在本流派工具箱里的技术"——结构派不出现 genogram,萨提亚不出现 enact。
- `psychiatrist` 在督导会时强制 cross-check 学员干预是否属于选定流派工具箱——越界时显式提示。
- 测试 `test_school_differentiation` 持续跑,W2 后加入 LLM-as-judge 守门。

---

## 4. v0.4 人类专家校准

### 4.1 邀请 2~3 位家庭治疗督导师

**目标**: 把"AI 评估"锚定到"人类评估"。

**校准对象**:
- 督导会输出的"成功点 / 失败点"
- 旁观者 hint 的"采纳价值"
- 复盘报告的"学习收获"

**治疗方向优化**:
- **抽样 50~100 个 session** 给督导师标注,形成金标准数据集。
- **每周校准循环**: AI 评估与人类评估一致性 > 70% 才算"可对外"。
- **失败案例必须复核**——AI 评高分但人类评低分的,回流训练数据。

---

## 5. v1.0 机构版

### 5.1 商业化关键能力

- **班级管理**: 一个培训机构开 N 个班级,每班 30 学员
- **报告对比**: 学员 A vs 学员 B 在同一案例上的表现横向对比
- **督导师工作台**: 督导师可以审阅学员的 session,做"二次督导"
- **付费墙**: 个人 ~500 元/年,机构 ~5 万/年

### 5.2 治疗方向优化

- **多模态**: 治疗室的"非语言"线索(沉默长度、叹气、语调)用 ASR/TTS 接入
- **跨流派对比训练**: 同一学员在同一案例上跑结构派 + Bowen + 萨提亚,横向看自己风格倾向
- **案例-学员匹配**: 根据学员的薄弱流派,推荐案例

---

## 6. 治疗方向的核心优化原则

1. **"家庭系统的真实性" > "理论覆盖度"**——宁可少一个理论点,也要让家庭动力学跑得真实。
2. **"督导会的密度" > "治疗室的轮数"**——5 轮督导会的理论价值可能高于 20 轮治疗的徒劳。
3. **"旁观者 hint 的可操作性" > "旁观者 hint 的频率"**——3 条可操作 hint > 10 条空泛观察。
4. **"经验库的可检索性" > "经验库的大小"**——100 条标注清晰的 > 10K 条噪声数据。
5. **"评估的人类校准" > "评估的自动化程度"**——AI 评估的天花板由人类金标准决定。

---

## 7. 风险与缓解（治疗方向专属）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **AI 角色人格崩塌**（治疗中途跑偏） | 高 | 共享记忆 + 强制节律 + 编排器兜底（PRD §14）；W2 加 LLM 输出的 validator（参考 `psychiatrist_prompts.py::test_psychiatrist_validator_rejects_first_person_affect_and_advice`） |
| **流派被错误混合** | 中 | 流派规则硬编码 + 心理医生 Agent 监督（v0.2）+ LLM-as-judge 守门 |
| **psychiatrist 抢戏**（在治疗室发言或越界指导治疗） | **新增** | `psychiatrist.should_speak()` 在治疗室永远 False；督导会子图显式构造 `SessionContext` 不暴露家庭角色 |
| **observer 过度干预**（hint 太频繁,医生变成提线木偶） | **新增** | `confidence < 0.6` 的 hint 不显示；`max_hints_per_round=1` 硬约束 |
| **学员路径依赖**（依赖旁观者提示,失去自主判断） | **新增** | v0.2 加"关闭旁观者"开关；v0.3 加"延迟 5 秒显示 hint"模式 |
| **伦理风险**（学员尝试让 AI 做真实咨询） | 中 | 明确产品定位 + 免责声明 + 不开放自定义案例（PRD §14） |
| **LLM 成本失控** | **新增** | 单 session ~12.5K tokens 估算；gpt-4o-mini 约 ¥0.04/次；月度 10K session = ¥400；v0.2 加 cost tracking |

---

## 8. 治疗方向 KPI（W2 → v0.2 → v0.3 → v1.0）

| 阶段 | 治疗方向 KPI | 目标 |
|---|---|---|
| W2 | 单次治疗可流畅跑完 20 轮 | 100% 不崩 |
| W2 | 旁观者 hint 准确率（人工抽样） | > 60% |
| W2 | 督导会与人类督导师一致性 | > 70% |
| v0.2 | L1 规则库经过 10 次治疗后,提示接受率 | 显著提升（p<0.05） |
| v0.3 | 案例库覆盖 5+ | 5 个 |
| v0.4 | 校准数据集规模 | > 100 session |
| v1.0 | 机构试用数 | 1 家 |

---

*版本：v1.0 · 与 PRD §15 后续演进路径一致。W2 启动时按 §1 的 3 个子任务优先级排期。*
