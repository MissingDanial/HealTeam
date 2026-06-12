# packages/prompts — Agent & 编排器 Prompt 工程

> HealTeam 的"治疗团队"由 5 个 LLM 角色 + 1 个编排器组成。本目录是他们的
> **system prompt 文本**——把人格、规则、边界、节律全部写在 markdown 里，
> 代码只负责**加载**和**校验**，不参与创作。

---

## 0. 目录结构

```
packages/prompts/
├── README.md            ← 本文件
├── doctor.md            ← AI 主治医师
├── psychiatrist.md      ← AI 心理医生
├── observer.md          ← AI 旁观者
├── father.md            ← AI 父亲
├── mother.md            ← AI 母亲
├── patient.md           ← AI 病人（青少年）
└── orchestrator.md      ← 编排器（决定谁说话、何时结束）
```

```
data/schools/
├── structural.md        ← 结构派（Minuchin）
├── bowen.md             ← Bowen 系统式
└── satir.md             ← 萨提亚
```

---

## 1. 每个 prompt 的设计要点

### 1.1 共用骨架（6 节）

每个 agent prompt 都包含 6 个固定章节，便于**结构化测试**和**人审**：

| 节 | 作用 | 例子 |
|---|---|---|
| **1. 你是谁** | 角色身份、人物背景、防御模式 | "你是 48 岁的工程师李建国..." |
| **2. 核心职责** | 表格化，4~6 条 | "控场、定调、决定干预时机、收尾" |
| **3. 行为约束** | ✅ 必做 / ❌ 绝不做 各 4~8 条 | "✅ 让家庭自己看见 / ❌ 不下诊断" |
| **4. 输出格式** | 字数、语气、提问风格、内部标签 | "50~150 字；带 `<!-- technique -->` 注释" |
| **5. 一致性约束** | 跨多轮 / 跨多次会话 的稳定性 | "不要在第 5 轮就松动防御" |
| **6. 与其他 Agent 的边界** | 表格化 6 行 | "旁观者：接收 hint，不照搬" |

### 1.2 各 agent 的差异化设计

| Agent | 字数硬上限 | 关键句式 | 防御 / 姿态 |
|---|---|---|---|
| **doctor** | 200 | "我听到...了" / "你们家...怎么运作" | 无；治疗师是**观察者** |
| **psychiatrist** | 350（督导会） | "正如 Minuchin (1974) 所述..." | 理论引用 + 教学语气 |
| **observer** | 120（旁路） | **永远**以"我注意到..."开头 | **不**下定论 / **不**评判 |
| **father** | 120 | 短句 + 口语（"嘛"、"啊"） | 合理化 / 否认 / 理智化 / 转移 |
| **mother** | 150 | 情绪化（"我每天..."、"撑不住了"） | 过度保护 / 压抑愤怒 / 躯体化 |
| **patient** | 80（默认 40） | 5~30 字沉默 / 突破时 30~60 字 | 回避 / 躯体化 / 退行 / 反向形成 |

### 1.3 防御 / 姿态模式的"强度"

- **低强度（开场）**：每个家庭成员按其"主旋律"防御 1~2 次。
- **中强度（中期）**：治疗师追问后，防御会**重复**或**升级**——这是学员要"接住"的素材。
- **高强度（突破）**：当治疗师**精准追问 3~5 轮后**，防御可能"松动"（以"其实我也..."开头），但**不**彻底瓦解。
- **跨多次会话**：防御**累积**——上次治疗师破了一个口子，下次家庭成员会**预先堵上**。

### 1.4 observer 的"我注意到..."为何是硬约束

- **句式一致性**是旁观者最重要的标志——学员看到治疗师忽然说出"我注意到..."就**立即知道**这是旁路提示被"化用"了。
- 该句式**只能**用**第三人称视角**的事实陈述——"父亲在过去 3 轮中重复了 4 次'别的孩子'"是 OK 的；"父亲是控制型"是**禁止**的。
- 详细的 9 类触发标签（`stagnation_3_rounds`、`defense_escalation` 等）见 `observer.md` §4.1。

### 1.5 编排器（orchestrator.md）的"业务规则化"

`orchestrator.md` 既是 W1 stub 期的**业务规则文档**，也是未来升级到 LLM 决策时的 system prompt。当前 W1 实现是**纯 Python 硬规则**（见 `packages/agents/orchestrator.py`），但**§2.2 决策矩阵**与代码一一对应。

升级到 LLM 决策时：
- 截取 §1 + §2 + §5 + §6 作为 system prompt
- user message 注入 `{session.round, session.events[-3:], last_event.actor, ...}`
- LLM 输出 `{"next_speaker": "father", "reason": "patient silent 3 rounds"}`

但当前 W1 **不推荐**升级——LLM 决策不可靠，硬规则更可控。

---

## 2. 3 个流派的差异化设计

> 关键设计原则：**3 个流派不能混用**——结构派不引用 Bowen 概念，反之亦然。

| 维度 | 结构派 | Bowen | 萨提亚 |
|---|---|---|---|
| **看什么** | 当下互动模式 | 代际传递 | 沟通姿态 + 感受 |
| **改什么** | 边界、子系统 | 自我分化 | 沟通一致性 |
| **第一句话** | "你们家**现在**在**怎么运作**？" | "从**爷爷那辈**说起——他是什么样的人？" | "你最想**被听到**的一句话是什么？" |
| **代表技术** | enact, boundary, triadic | genogram, differentiation, detriangulation | iceberg, congruence, parts party |
| **治疗师姿态** | 主动、控场、节制 | 低情绪、思考者、不站队 | 温暖、镜映、接纳 |
| **任务布置** | 行为任务 | **不**布置 | 自我观察 |
| **理论之父** | Minuchin | Bowen | Satir |

### 2.1 干预技术清单（每派 8 个，重叠 < 50%）

| 结构派 | Bowen | 萨提亚 |
|---|---|---|
| joining 加入 | genogram 家谱图 | communication stance 沟通姿态 |
| tracking 追踪 | circular question 循环提问 | iceberg model 冰山模型 |
| enactment 结构化提问 | triangulation question 三角化提问 | feeling reflection 感受镜映 |
| boundary making 边界重塑 | differentiation question 自我分化提问 | self-mandala 自我环 |
| triadic question 三角化提问 | detriangulation 去三角化 | congruent communication 一致性沟通 |
| reframing the symptom 重构症状 | projection question 代际投射提问 | warmth 温度 |
| intensity modulation 强度调整 | self-in-relation 关系中的自我 | transformation 转化 |
| task assignment 任务布置 | process observation 过程性观察 | family sculpting 家庭雕塑 |

> 唯一**术语层面**重叠：**三角化 / 三角关系**——它在结构派是"提问技术"，
> 在 Bowen 是"理论概念"。这反映**真实学术**——Bowen 的 triangulation
> 概念本身就启发了 Minuchin 的 triadic question。我们用**括号注释**区分
> （`triangulation question / 三角关系提问` vs `triadic question`），避免
> 学员误以为两派有相同的"三角化"技术。

### 2.2 节律差异

- **结构派**：开场 5 轮 / 中期 10 轮（边界工作）/ 收尾 5 轮（任务）。**严格 20 轮**。
- **Bowen**：开场 5 轮（家谱图）/ 中期 10 轮（去三角化 + 分化提升）/ 收尾 5 轮（**思考题**，不是任务）。
- **萨提亚**：开场 5 轮（加入 + 感受确认）/ 中期 10 轮（冰山 + 一致性演练）/ 收尾 5 轮（**自我观察作业**）。

> 三派都**目标 20 轮**——但**收尾环节的"作业"**完全不同：结构派布置**行为任务**，Bowen 给**思考题**，萨提亚给**自我观察**。

### 2.3 旁观者的"流派特化"

每个流派模板 §7 都列出了**该流派下**的 6 类 observer 触发 → 提示话术映射。例如：

- 结构派督导下，observer 听到"边界重塑"是**合规**的；听到"代际投射"是**`school_mixing`**。
- Bowen 督导下，observer 听到"代际投射"是**合规**的；听到"边界重塑"是**`school_mixing`**。
- 萨提亚督导下，observer 听到"感受镜映"是**合规**的；听到"代际"是**`school_mixing`**。

这套**流派特化的 hint** 是 L1 进化引擎的**关键**——它决定了"提示接受率"在不同流派下能被分别测量。

---

## 3. 如何测试 prompt 是否"够用"

### 3.1 三层测试

| 层 | 工具 | 测什么 |
|---|---|---|
| **结构测试** | pytest + 文件读取 | prompt 文件存在、字数 ≥ 1500、6 节齐全、关键词命中 |
| **校验器测试** | pytest + 角色 validator | 给定"好的"和"坏的"输出示例，validator 正确分类 |
| **LLM 端到端** | （W2）真 LLM | 完整跑一个 20 轮治疗，输出可读、有流派特征 |

W1 + agent-prompts 任务交付的是**前两层**——`tests/prompts/` 36 个用例。

### 3.2 怎么判断一个 prompt"够用"

> **核心标准**：如果**结构测试 + 校验器测试**全过，但**真 LLM 跑出来的输出**不
> 像该角色，**那是 prompt 不够好**——而不是测试不够严格。

具体可观察的失败模式：

| 失败模式 | 调整方向 |
|---|---|
| 医生开始给建议 | 加 `❌ 我建议` 黑名单 + 加 5~10 条"应该问而不是说"示例 |
| 旁观者开始下定论 | 加 `❌ 控制型人格` 黑名单 + 强制"我注意到"开头 |
| 心理医生在 live session 说话 | 检查 `should_speak` 是否永远返回 False |
| 家庭成员用专业术语 | 加 `❌ 投射` `❌ 自我分化` 黑名单 + 加"说人话"示例 |
| 流派混用 | 给 observer 加 `school_mixing` 触发 + 在每个流派 §6 列出"不能用本派的术语" |

### 3.3 跑测试

```bash
# 跑全部 prompt 一致性测试
py -m pytest healteam/tests/prompts -v

# 跑某一个 agent 的
py -m pytest healteam/tests/prompts/test_doctor_prompts.py -v

# 跑学校模板
py -m pytest healteam/tests/prompts/test_school_templates.py -v
```

W1 当前：**36 passed in 0.08s**。W1 不调用任何 LLM，所以测试**快、可重复**。

---

## 4. 如何迭代（哪个字段决定什么行为）

> 这是 prompt 工程的**核心方法论**——**不要**改完整个 prompt，而是**精确定位**改哪一节。

| 想改变的行为 | 改哪一节 | 例子 |
|---|---|---|
| 让角色**说不同的话** | §4 输出格式（语气 / 字数 / 句式示例） | 字数从 50~150 改到 80~200 |
| 让角色**做不同的事** | §2 核心职责（表格） | 加一行"治疗师必须在开场自我介绍流派" |
| 让角色**不做的某事** | §3.2 行为约束的 ❌ 列表 | 加 `❌ 不要用'病'这个字` |
| 让角色**更稳定** | §5 一致性约束 | 加"第 5 轮不要突然反思" |
| 让角色**与其他 agent 关系**改变 | §6 与其他 Agent 的边界 | 改"旁观者：接收 hint，不照搬"为"可以**直接**说出 hint" |
| 流派相关 | 改 `data/schools/<school>.md`，**不**改 agent prompt | 改结构派的"开场话术" |

### 4.1 调试流（如果真 LLM 跑偏了）

1. **复现**：用同样的历史对话 + 同样的 LLM seed 跑 5 次，看 5 次都跑偏还是随机跑偏。
2. **定位**：跑偏的输出**违反** §3 行为约束中的哪一条？
3. **加反例**：在 §4 输出格式里加 1 个"反例 + 正例"对比。
4. **加硬约束**：在 §3.2 加 1 条 ❌ 黑名单（用触发短语 regex 而非整段）。
5. **加测试**：在 `tests/prompts/test_<role>.py` 加 1 个 validator 用例。
6. **重跑**：再跑 5 次，确认跑偏率下降。

### 4.2 不要做的事

- ❌ **不要**改完整个 prompt 才测试——每次只改 1 节。
- ❌ **不要**把规则写在"开场白里"——LLM 会**抄**开场白（行为约束在 §3 才有效）。
- ❌ **不要**用模糊语言（"尽量"、"可能"）——LLM 会跳过。改用"必须"、"绝不"、"硬上限"。
- ❌ **不要**在中文 prompt 里塞英文规则——LLM 会**优先**遵循英文规则，但**输出仍是中文**——这造成"双标"。**统一用中文**。
- ❌ **不要**用 `>` 引用块**写规则**——LLM 会以为是"示例"而不是规则。**用 `❌` `✅` 列表**才有效。

---

## 5. Prompt 与代码的契约（架构边界）

> 这一节给 W2 / W3 的开发者看——prompt 文件**不**是"参考文档"，
> **是**架构契约。

```python
# packages/agents/base.py
def load_system_prompt(path: str) -> str:
    """Read the system prompt text from disk.
    
    Path resolution:
    - absolute → return as-is
    - relative → resolve under packages/prompts/
    """
    p = resolve_prompt_path(path)
    if not p.exists():
        return ""    # W1: empty is OK; W2 should treat as error
    return p.read_text(encoding="utf-8")
```

**契约**：
1. agent 设置 `system_prompt_path = "doctor.md"` 等相对路径——**不**用绝对路径。
2. agent 不在 import 时读 prompt——**懒加载**（避免冷启动慢）。
3. prompt 文件**永远**是 UTF-8 markdown，**不**带 frontmatter，**不**带 YAML。
4. 修改 prompt 文件**不**需要改 agent 代码——**不**会出现"prompt 改了但代码没改"的同步问题。

### 5.1 与 LLMClient 的接口

W2 真实集成时，agent 的 `respond(ctx)` 大致是这样：

```python
async def respond(self, ctx: SessionContext) -> str:
    system = load_system_prompt(self.system_prompt_path)
    messages = build_messages_from_ctx(ctx)  # 历史对话 + 触发问题
    raw = await self.llm.chat(
        system=system,
        messages=messages,
        temperature=0.7,
        max_tokens=300,
    )
    # Optional: 截断到本角色硬上限
    return raw[:DOCTOR_HARD_MAX_CHARS]
```

W1 stub 不实现 `respond` 的 LLM 路径（`raise NotImplementedError`），所以 `tests/prompts/` 的测试**只**测 prompt 结构 + validator，**不**测 LLM 路径。

---

## 6. 字数统计（v1.0）

| 文件 | 字数 | 角色 |
|---|---|---|
| `doctor.md` | ~3200 | 主治医师 |
| `psychiatrist.md` | ~2700 | 心理医生 |
| `observer.md` | ~3300 | 旁观者 |
| `father.md` | ~2400 | 父亲 |
| `mother.md` | ~2400 | 母亲 |
| `patient.md` | ~2400 | 病人（青少年） |
| `orchestrator.md` | ~2200 | 编排器 |
| `data/schools/structural.md` | ~3500 | 结构派 |
| `data/schools/bowen.md` | ~3500 | Bowen 系统式 |
| `data/schools/satir.md` | ~3500 | 萨提亚 |

> 字数为参考值，每次小幅调整不影响 v1.0 契约。

---

*版本：v1.0 — 与 PRD §6 + architecture.md §4 对齐。*
