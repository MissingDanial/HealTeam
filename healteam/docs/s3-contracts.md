# S3 模型与用量约定

版本 0.4.0，数据库保持 schema v1。S1/S2 的持久会话、事件重放、幂等、取消和进程所有权规则继续适用。

## 配置与模型调用

`HEALTEAM_MODEL_MODE=mock` 为源码默认值；live 必须显式配置 provider、model 和本机私有 API key。DeepSeek 使用 `https://api.deepseek.com/v1`，OpenAI 使用 Chat Completions，Anthropic 使用 Messages。可配置兼容服务的 base URL。启动脚本加载根 `.env`，修改后重启后端。

DeepSeek 使用 `max_tokens` 并关闭 thinking；OpenAI 使用 `max_completion_tokens`；Anthropic 使用 `max_tokens` 与顶层 system。默认不发送 temperature，需要时用 `HEALTEAM_LLM_SEND_TEMPERATURE=true`。模型名由配置明确指定，不进行自动替换或供应商回退。

正式 `HTTPModelClient.complete()` 一次调用只发一次 HTTP 请求，不自动重试、不跟随重定向、不读取系统代理；限制请求时长和响应字节数。只读取最终文本，拒绝空响应、异常结构、截断、拒绝和未支持的结束原因，不把 reasoning_content 当作角色发言。历史 `.chat()` 仅为默认停用的 S0 入口兼容，正式训练不用它。

接口依据：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Anthropic Messages](https://platform.claude.com/docs/en/build-with-claude/working-with-messages)、[Anthropic 停止原因](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

`/readyz` 只检查本地配置、资源和存储，不发送付费模型请求，不代表余额或实时上游状态。缺密钥的 live 实例仍能读取历史和取消已保存会话，但不能创建/继续模型生成。

## 固定会话条件与上下文

创建会话保存 model_mode、provider、model、端点指纹、调用预算、输出/上下文限制，以及选定流派与角色提示词资源。密钥和 base URL 明文不写入数据库或公共响应。端点只保存 SHA-256 指纹；更换密钥可继续，更换模型、provider、端点或切换 mock 后不能给原 live 会话悄悄换引擎。

人工练习只生成家庭回应，学员担任治疗师；自动示范才生成 AI 治疗师。家庭角色收到自己的背景与其他角色的公开资料，不收到其他人的私有恐惧或原生家庭笔记。治疗师示范只使用公开病例信息。

提示词以病例姓名覆盖模板示例姓名，使用单一选定流派，明确学员文本是数据。上下文按 `HEALTEAM_CONTEXT_MAX_CHARS` 限制，优先保留最近的完整发言；基础角色资料已超限时直接失败，不静默改角色或混用流派。报告证据只针对实际送入模型的发言窗口验证，不能引用模型未见到的旧发言。

模型返回完整消息后进行 JSON 校验，通过才作为一条持久事件发布；界面的 SSE 传输已验证事件，不直接转发未经验证的 token 流。角色输出必须匹配指定 actor，长度有限，不允许显式夹带其他角色/病例姓名标签的多角色脚本。

## AI 辅助复盘

mock 报告 kind 仍为 `workflow_check`；live 为 `ai_review`。新增 structured：

```json
{
  "summary": "简短回顾",
  "observations": [{"text": "可观察的互动", "evidence_seq": [3]}],
  "next_steps": [{"text": "下一次练习可探索的问题", "evidence_seq": [3]}]
}
```

拒绝未知字段、空证据、重复或虚构序号。证据必须是本次发给模型的公开 utterance，不能引用内部用量事件。格式或证据失败时不保存成功报告，可按原 S2 规则单独重试报告并继续累计调用。

界面和报告均标识“未经专家校准”，自动示范额外标明不是学员成绩。结构、角色标签和引用校验不保证语义、流派仿真或教学评价有效性；本阶段不提供专业评分。

## 用量与预算

每次 live 调用前在同一会话写事务中保存内部 `model_call_started`，预留调用次数及输入估计与输出上限。获得结果后保存 `model_call_finished`，包含可解析的输入/输出 token、耗时及安全错误码，不包含原始请求/响应正文、URL、认证信息或思维链。

两个事件只能是 internal system 事件，不进入浏览器快照的 events 或 SSE。序号过滤后允许间隙；公共 `usage` 汇总包含 requests、input_tokens、output_tokens、unknown_requests、reserved_tokens、max_calls、token_budget。

调用次数不因失败、取消、重启或复盘重试清零。未知输入与未知输出分别保留各自预留；已知较大的输入不能吃掉未知输出的预留，反之亦然。若上游响应可解析 usage，即使角色/报告校验失败，也先保存已知用量。取消/超时后迟到结果不落库，原预留继续占预算。

默认上限：128 次调用、250000 token 预算、每次输出 1024 token、上下文 30000 字符、响应 262144 字节。分别由 `HEALTEAM_MODEL_MAX_CALLS`、`HEALTEAM_MODEL_TOKEN_BUDGET`、`HEALTEAM_LLM_MAX_TOKENS`、`HEALTEAM_CONTEXT_MAX_CHARS`、`HEALTEAM_LLM_MAX_RESPONSE_BYTES` 配置。

token 预留使用保守输入估计，并非供应商 tokenizer 或账单；未知用量不显示为零。统计不计算金额、不承诺绝对费用上限，实际费用以供应商为准。预算超限在下次发请求前拒绝，保留已提交输入和记录。

## 验证边界

自动化测试和浏览器完整流程使用仅监听本机的双协议 fixture。fixture 支持鉴权、限流、坏 JSON、错误角色、截断和虚假证据注入，没有生产调试入口。浏览器脚本必须显式核对 fixture 标识、回环地址和专用模型名，防止误对付费服务跑自动验收。

按用户提供的 DeepSeek 凭据完成一次简短真实连接测试；多轮质量、专业督导校准、长期上游稳定性、Docker 和多人部署仍不在本阶段验收范围。
