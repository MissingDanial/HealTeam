# W1 遗留 Issue 清单（修订版）

> 路径：`healteam/docs/w1-issues.md`
> 报告日期：2026-06-03
> 修订日期：2026-06-03（v1.1，与 w1-integration-report.md 一致）

按优先级分：阻塞 / 重要 / 次要 / 后续优化

> ⚠️ **重要说明**：v1.0 把"内容资产 + 接口契约"算作"重要"问题，但在 v1.1 里这些已经归到 W1 PASS 范围。本表只列 W2 必做或后续优化的事项。

---

## 阻塞（必须 W2 修）

> 无真正意义上的"阻塞"——W1 已交付**可运行骨架**，所有未做项都属于"做完了才是完整产品"。

---

## 重要（W2 必做，影响"可演练"的核心行为）

### I-1: 旁观者 hint 暂时是空操作
- **症状**：ObserverAgent.observe() 在 W1 返回 None，observer_hint 节点每轮被调用但不产生内容
- **影响**：学员陪练时旁观者不会主动提示"家庭陷入僵局了"、"流派术语被混用了"等关键信号
- **W2 修复**：在 W1 已落盘的 observer.md prompt 基础上接入 LLMClient，observe() 返回基于 prompt 的结构化 Hint
- **预估工作量**：1 周（含 LLM 接入 + 端到端测试）

### I-2: 心理医生在治疗中不发言（设计约束但 W2 必做）
- **症状**：psychiatrist 在 should_speak 里始终 False，30+ 次 W1 集成测试都观察到这一行为
- **影响**：W1 治疗过程中没有督导会，学员演练时无法看到"理论支撑"环节
- **W2 修复**：治疗结束后触发 3 Agent 督导会（doctor / psychiatrist / observer），产出"督导共识"摘要
- **预估工作量**：1 周（与 I-1 一起做）

### I-3: doctor 在 W1 用 placeholder monologue
- **症状**：W1 demo 显示 20 轮几乎全 doctor 说话，family 不说话（设计如此）
- **影响**：W1 不能用于真实演练；W2 切真 LLM 后才能体验真实家庭互动
- **W2 修复**：在 doctor.py 接入 LLMClient，从 doctor.md 读 system prompt，构建 messages 后调 LLM
- **预估工作量**：3~5 天

---

## 次要（演示层面的小问题）

### I-4: 演示输出没有演示性内容
- **症状**：W1 demo 输出形如 `[doctor placeholder | round=0]`，对外部观众没有可读性
- **影响**：演示给领导/投资人看时需要手动解释
- **W2 修复**：可选——W1 写个 mock LLM 输出预设对话流作为"剧情模式"演示
- **预估工作量**：半天

### I-5: 测试 fixture 用了 31 个 case 测试
- **症状**：tests/cases/ 用了 pytest parametrize 跑了 18+ 流派测试用例，运行 0.05s
- **影响**：低；但风格可以更紧凑
- **W2 评估**：维持原样或合并

### I-6: 集成测试用 orchestrator.run() 同步跑
- **症状**：20 轮 mock run 是同步调用；W2 接真 LLM 后会慢很多
- **影响**：W2 需要改 run() 为 async 或 streaming
- **W2 修复**：引入 asyncio + WebSocket 流式输出
- **预估工作量**：1 周

---

## 后续优化（v0.2 / v0.3）

### O-1: 没有人类专家校准循环
- **症状**：所有 LLM 评估都是自动化的，没有人类家庭治疗督导师的"金标准"
- **影响**：评估准确度的天花板受限于自动评估
- **v0.2 修复**：邀请 2~3 位家庭治疗督导师，标注 50~100 个治疗会话
- **预估工作量**：2~4 周（含伦理审查、专家对接）

### O-2: 共享记忆目前是 in-memory
- **症状**：`MemoryStore` Protocol + `InMemoryStore` 是 stub，没有接 pgvector
- **影响**：治疗结束后数据丢失；不能跨会话学习
- **v0.2 修复**：实现 PostgresMemoryStore + 经验库检索
- **预估工作量**：2~3 周

### O-3: 旁观者 L1 进化机制暂未实现
- **症状**：PRD §7 设计的"经验库 + 规则权重"暂未实现
- **影响**：旁观者不会自我进化
- **v0.3 修复**：实现经验库写入 + 规则权重更新
- **预估工作量**：2 周

### O-4: Web UI 仅占位
- **症状**：`apps/web/app/page.tsx` 是 server-render landing page，没有真实交互
- **影响**：v0.1 没有可用 UI 给学员用
- **v0.2 修复**：实现治疗会话 UI（Next.js 14 + WebSocket）
- **预估工作量**：3~4 周

### O-5: 学员账号 / 班级管理未实现
- **症状**：没有 user system
- **影响**：v0.1 多人无法共享数据
- **v0.3 修复**：FastAPI Users + Postgres 账号表
- **预估工作量**：2 周

---

## 已规避的风险（PRD §12）

| 风险 | 规避方式 |
|---|---|
| ❌ 启 uvicorn 前台卡住沙箱 | demo_w1.py + smoke.py 都不启服务，只调模块函数 |
| ❌ 案例内容凭空捏造 | 所有防御模式都标了 underlying_fear，origin_family_note 引用林建国父亲 |
| ❌ 流派术语混用 | 每个流派模板 §2.1 明确"不在本流派工具箱里的技术" |
| ❌ 学员直接面对真病人 | MVP 范围明确"AI 仿真"，不接真人 |
| ❌ LLM-as-judge 评分失真 | 关键质量门用"实际跑测试"而非"LLM 评 LLM" |

---

*版本：v1.0 · W1 收尾*
