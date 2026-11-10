# S4 试行评估与人工复核约定

版本 0.5.0。S4 提供教学观察与本机人工标注工具，没有完成真实专家校准，没有总分、认证结论或临床疗效评价。

## 试行量规与报告

四个维度为对话参与、探索表达、流派一致、合作下一步。项目自写的 0/1/2 行为锚点保存在 `data/rubrics/trial-family-training-v1.json`；null 表示证据不足，不能换算为 0。缺少展示不等于能力不足。

病例观察目标按所选流派筛选，冻结为该会话的 rubric。目标不是预定结果，不能用病例背景或预期轮次当作已经发生的证据。观察为 observed 时，证据必须包含目标所需家庭角色；人工训练的非 null 维度评级必须引用学员发言。

量规参考了家庭治疗训练中参与、反馈、理论与实践联系等一般方向；其内容由本项目编写，不是 AAMFT 标准量表，也未获得该机构认证。[AAMFT Core Competencies（2004）](https://www.aamft.org/common/Uploaded%20files/COAMFTE/Accreditation%20Resources/MFT%20Core%20Competencies%20%28December%202004%29.pdf) 可供概念背景核对；专业能力与督导的边界可参照 [AAMFT 2026 Code of Ethics](https://www.aamft.org/common/Uploaded%20files/Legal%20Ethics/AAMFT%20Code%20of%20Ethics.pdf)。这些参考不构成本软件的有效性证据。

新 live 报告仍为 kind=ai_review，保留 S3 structured，并新增顶层 rubric 与 assessment。模型在原复盘请求中返回 dimensions 和 milestones；版本、哈希、实际 reviewed_event_seq 与 total_utterances 由后端注入。拒绝布尔评级、越界值、重复/遗漏维度、错误里程碑、虚构序号及角色证据不符。

新 mock 报告有 rubric，assessment 为 null，可人工复核但不生成 AI 评级。旧 S3 会话没有冻结量规，继续原报告格式，不追补评级。pending/failed 报告不提前公开病例目标；只有成功保存的报告返回量规。

新量规复盘使用 `HEALTEAM_REVIEW_MAX_TOKENS=4096`，家庭回应仍用原 `HEALTEAM_LLM_MAX_TOKENS`。两者均占同一持久预算；旧会话保留原输出限制。

## 人工复核

`GET /api/v1/reports/{id}/reviews` 返回 session_id、report_hash、rubric_hash、latest_revision 和按修订顺序排列的 items。没有修订时 latest_revision=0。旧版无量规或未完成报告返回 review_unavailable。

`POST` 同路径提交：

```text
client_review_id, expected_revision, report_hash, rubric_hash,
reviewer_name, dimensions, milestones, notes
```

dimensions 精确包含四维，每项为 dimension_id、rating、rationale、evidence_seq。milestones 精确匹配该量规目标，每项为 milestone_id、status、rationale、evidence_seq；status 为 observed / not_observed / insufficient_evidence。

新修订返回 201，相同 ID 与相同内容重放返回 200；同 ID 不同内容拒绝。expected_revision 不匹配返回 409 / review_revision_conflict，并提供 current_revision。报告/量规绑定失效也拒绝，但不作为可直接更新基准的普通版本冲突处理。

复核保存为追加记录，不修改原 AI 报告、对话、会话 revision 或模型用量；已有活跃训练不阻塞旧报告人工复核。证据只允许公开 utterance，不能引用内部账本或状态事件。人工可查阅整段对话，模型评价只覆盖实际输入窗口。

复核者姓名由本机填报，reviewer_identity_verified 永远为 false，系统没有核实资质。demo 可保存观察复核，但不是学员成绩。没有多用户身份或权限隔离。

前端在当前标签页保存复核草稿及待确认请求 ID；响应丢失时先核对或按同 ID 重放。409 保留草稿，读取最新修订后由用户明确更新基准，生成新请求；读取失败可在页内重试。迟到成功响应只清理匹配 ID 的草稿，损坏的浏览器存储不能导致页面崩溃。

## 统计与导出

`GET /api/v1/calibration` 返回描述性统计，status 始终为 awaiting_expert_calibration。只对人工训练、live、已有有效 AI assessment 的报告建立配对；mock、demo、旧报告不进入学员 AI 一致率。

每报告只取最新人工修订。AI 和人工都非 null、且人工证据位于 AI 实际参考窗口内的维度才进入 paired_dimensions；精确一致率为 exact_matches / paired_dimensions。证据不足单列 insufficient_pairs，窗口不匹配单列 out_of_window_pairs；无有效配对时一致率为 null。修改人工修订不会增加样本数。

按 rubric_hash、rubric_version、school、provider、model 分组。修订哈希不匹配、损坏的人工记录视为尚无可用人工配对；损坏的 AI 评价或量规不计入有效样本，单条空报告不使整批统计崩溃。高一致率、甚至 100%，都不自动改变待校准状态。

`GET /api/v1/calibration/export` 是用户主动下载的去标识化 JSON。包含评级、状态、证据序号、实际窗口、量规/模型分组、最新修订号及样本摘要 ID；不包含原对话、姓名、rationale、notes、密钥、URL 或病例私有资料。摘要 ID 用于关联导出样本，不是不可再识别的保证。

total_sessions 统计已保存报告；eligible_sessions 是可用于学员 AI 校准的有效报告；reviewed_sessions 是其中具有匹配最新人工复核的报告。界面不会把它当作全部本机人工复核数量。

## 数据库与版本

schema v1→v2 在持有 RuntimeLease 的写事务中追加 training_expert_reviews 表，保留原四类数据：会话、事件、收据、报告。数据库版本与 DDL 一起提交；失权或迁移失败全部回滚。未知版本、缺列和非预期同名表拒绝，不覆盖。

升级前停止后端并备份数据库。旧版本只认识 v1，回退应恢复升级前备份。新评审数据与训练库都不进入源码包或镜像构建上下文。

## 校准边界

本轮基准是明确标识的合成软件样例，只验证计数、绑定、证据约束与流程。真实专家标注样本数为 0。完成工具开发不等于完成量表有效性或临床校准；真实专家需另行审阅适用性、标注规则和代表性样本。
