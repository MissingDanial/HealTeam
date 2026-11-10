# S1 本地离线训练接口与运行约定

本阶段实现单病例、单人、本地文件 SQLite 的训练闭环。角色回应和报告都是明确标识的确定性模板，运行方式为 mock；没有接入真实模型、专业评分或自我学习。

## 入口和模块

正式页面为首页和 `/sessions/{id}`。正式业务路由在 `apps/api/app/routers/training.py`，运行管理在 `services/training_manager.py`，持久化在 `packages/memory/training_store.py`。历史 `routers/sessions.py` 是默认停用的 S0 适配器；历史 reports.py 不再挂载。

当前响应模型在 `response_models.py`，共用请求、状态、事件模型在 `memory/contracts.py`。`docs/contracts` 的 JSON Schema 和样例由脚本导出。

## 请求和响应

| 操作 | 行为 |
|---|---|
| 创建 | POST /sessions，CreateSessionCommand，首次 201，相同请求 200 |
| 历史 | GET /sessions?limit=20&offset=0，返回 items 与 total |
| 快照 | GET /sessions/{id}，返回平铺 SessionView 与可见 events |
| 发言 | POST /sessions/{id}/messages，SubmitMessageCommand，接受后 202 |
| 正常结束 | POST /sessions/{id}/finish，至少完成一轮且等待输入时可用 |
| 取消 | POST /sessions/{id}/cancel，幂等，已提交内容保留 |
| 事件 | GET /sessions/{id}/events?after=seq，支持 Last-Event-ID |
| 报告 | GET /reports/{id}，存在会话时返回报告状态对象 |
| 重试复盘 | POST /reports/{id}/retry，仅失败的 supervision/report 可重试 |

实际路径统一带 `/api/v1`。报告 kind 固定为 workflow_check。未生成报告的 content 为 null。

创建使用 client_request_id；发言使用 client_message_id 和 expected_turn。重复请求检查先于忙碌、终态和当前资源预检；相同 ID 不同请求内容返回 409。expected_turn 是下一条待接受干预的回合编号。

未知病例 404；参数不合法 422；轮次/状态/幂等/单活跃冲突 409；配置或存储不可用 503。错误仅返回稳定 code，不泄露密钥、内部路径和上游异常。

## 模式与轮次

training 创建后等待学员。每条有效干预接受后赋予 1 基轮号，生成一至三个家庭模板回应，然后回到等待；AI 医生不参与练习发言。

demo 自动产生 AI 医生与家庭回应，报告明确不是学员成绩。两种模式通过同一数据库约束共享一个活跃槽，waiting 也占用该槽。

current_round 是当前/最近接受的回合，completed_rounds 只在整组回应完成后推进。中断回合保留，不能假装完成。

## 状态和生命周期

正常路径：created → awaiting_input/running → simulation → supervision → report → completed。阶段仅前进；completed 必须与报告和终态事件在同一事务提交。

取消先提交 cancelling，再停止任务并进入 cancelled。完成已提交则取消不改变结果；取消已提交则不能再写完成。已接受后等待交接的操作由同一个 worker 串行接手，不能因上一轮任务还未退出而遗漏。

关闭页面仅断开订阅，不取消会话。重启后 waiting 可继续；created/running/cancelling 变为 interrupted，已有发言保留，不自动补发半轮。报告生成中停机会标记报告失败，避免永久显示生成中。

## 数据与事件一致性

schema v1 包含会话、事件、收据三张表；病例快照、配置快照和报告保存在会话记录中。0→1 初始化有版本守卫，未知版本及无版本的其他数据库被拒绝。

单活跃 owner 索引、SQLite 写事务及 revision 检查共同约束修改。每操作独立 AsyncSession；事务内不执行模型或其他外部异步工作。

事件先提交再通知。seq 在会话内单调增加，可见性过滤后允许间隙。快照与事件游标来自同一提交视图。SSE 使用 no-cache,no-transform，避免前端代理 gzip 缓冲；订阅不会创建或重启训练。

S1 通过持久事件轮询提供实时订阅，具备基本心跳、游标重放和浏览器去重。高级网络故障、长时压力与更复杂恢复继续按 S2 加固，不以本轮通过的样本宣称覆盖所有故障。

## 输出边界

模板会引用学员当前表达，仅验证交互和保存过程。报告列出输入、回应与事件序号，不判断干预专业性、流派有效性和治疗成效。真实模型接入与专业证据评估按后续阶段完成。

SQLite 内容为本机记录，没有多人登录隔离；本地运行不等于机构部署。源码和镜像构建上下文不应包含本地训练数据库。
