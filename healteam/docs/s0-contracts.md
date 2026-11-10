# S0 数据契约与下一阶段接口边界

本规格服务于单人本地训练。S0 建立可执行的模型、校验规则和样例；S1 再接入会话管理与数据库。当前 legacy demo 接口与下述训练接口不能混用。

## 已运行的 S0 接口

| 路径 | 当前行为 |
|---|---|
| GET /healthz | 进程存活；返回脱敏模型模式状态 |
| GET /readyz | 离线检查配置与内容；不可用返回 503 |
| GET /api/v1/cases | 严格校验后返回病例列表 |
| GET /api/v1/cases/{id} | 返回规范化病例；未知 ID 返回 404 |
| GET /api/v1/agents | 六个角色 |
| POST /api/v1/sessions | 仅 mode=demo；200 SSE；范围 1～30 回合 |
| GET /api/v1/sessions | 现有进程内演示摘要，不是持久历史 |
| GET /api/v1/reports/{id} | 仍为 501，待持久化报告接口实现 |

请求中的训练模式会明确被拒绝，不能悄悄运行示范。

## S1 目标接口

模型源在 `packages/memory/contracts.py`，版本 0.2.0；JSON Schema 和样例位于 `docs/contracts`。通过 `python scripts/export_contracts.py` 更新，`--check` 检查是否同步。

| 路径 | S1 契约（尚未实现） |
|---|---|
| POST /sessions | CreateSessionCommand；先持久创建，再返回 201 和稳定 ID |
| GET /sessions/{id} | 一致性快照、公开历史及 last_event_seq |
| GET /sessions/{id}/events?after=seq | 重放后订阅，兼容 Last-Event-ID |
| POST /sessions/{id}/messages | SubmitMessageCommand；返回接受结果 |
| POST /sessions/{id}/finish | 等待输入且至少完成一回合时结束并复盘 |
| POST /sessions/{id}/cancel | 幂等取消，保留已提交记录 |
| GET /reports/{session_id} | 稳定会话 ID 查询 not_started/generating/ready/failed/cancelled |
| POST /reports/{session_id}/retry | 仅重试失败的督导或报告，不重跑模拟 |

实际路由统一使用 `/api/v1` 前缀。创建请求的 client_request_id 和发言的 client_message_id 都需要数据库唯一约束。相同 ID、相同内容返回原结果；相同 ID、不同内容返回 409。重复请求检查先于 busy/旧轮次/终态检查。

## 使用模式、轮次与事件

training：学员作为治疗师，AI 医生不得代发言；demo：AI 医生示范，不计为学员成绩。model_mode=mock/live 是服务端的另一项独立配置，不能由浏览器提交密钥或供应商地址。

一次训练回合是一条被接受的学员发言及其一组家庭回应。正式回合从 1 开始，背景说明可用 0。expected_turn 表示下一次待接受的回合编号。先接受输入、赋轮号，再构建任何角色输入；中断回合明确标记未完成。达到 max_rounds 后自动进入复盘。

事件包含 session_id、event_id、seq、round、type、actor、source、visibility、timestamp、payload。seq 在会话内单调递增；过滤私密事件后可出现序号间隙。先保存事件再通知浏览器；重连可以重传，前端不得重复展示。

source 区分 human/ai/system，避免将 AI 发言计为学员行为。房间发言是 public；观察者提示只能是 learner/supervisor/internal。家庭角色的模型请求只接收房间内公开发言及自己的档案。

## 状态与阶段

状态：created、awaiting_input、running、cancelling、completed、cancelled、failed、interrupted。

阶段：simulation → supervision → report，不能倒退。training 准备后进入 awaiting_input；demo 准备后进入 running。等待学员输入只属于 simulation。

正常结束进入 running/supervision，再进入 running/report；报告保存和 completed 必须原子提交。cancelling 已提交则禁止随后写 completed；completed 已提交则取消返回既有结果。终态不允许修改阶段或报告状态。

failed/supervision 和 failed/report 可以在保存的复盘步骤重试，failed/simulation 不自动重跑半轮。纯 `transition_snapshot` 校验不能代替事务；S1 必须在持久化层检查并发版本/当前状态。

刷新和关闭页面只断订阅，不代表取消。重启时 awaiting_input 可恢复；在途 created/running/cancelling 标记 interrupted，保留已有记录，不自动补发模型调用。

## 病例规范

`CaseDefinition` 是 API 预检与 Agent 共用的唯一模型。原始病例内容保持不变，加载边界显式迁移旧字段；同时提供新旧同义字段属于冲突，未知字段直接报错。

关键规范化字段：opening_context.scene/atmosphere/emotional_states；agent_role_hints.father/mother/patient；first_utterance_draft；milestone.description/validation_signals；family_pattern.structural/bowen/satir；defense.example。

会话需要保存病例、提示词及评估器版本。里程碑字段接通不等于已经实现专业评估；原文关键词命中仅用于原型流程。

## S0 与后续工作的边界

S0 仍使用旧同步角色调用和 POST SSE 演示，尚不保证断开浏览器后底层线程停止。适配器临时采用单请求 HTTP 客户端避免跨事件循环共享连接，完整异步与会话取消按阶段计划完成。

SQLite、持久事件、学员输入、幂等执行、重连重放、正式证据评估与报告查询不能仅因已有模型就宣称已实现。
