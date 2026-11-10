# HealTeam API — S5 本地交付

正式接口由 `app/routers/training.py` 提供，执行管理位于 `app/services/training_manager.py`，会话和报告保存到本机 SQLite。运行配置、启动方法与接口清单见[项目 README](../../README.md)，状态、幂等与恢复约定见 [S4 评估文档](../../docs/s4-contracts.md)。

在项目根目录使用 `.venv/Scripts/python.exe scripts/start_server.py` 启动，默认地址为 `http://127.0.0.1:8000`。`/healthz` 检查进程存活，`/readyz` 检查配置、资源和数据库可用性。

日常一键使用由 `scripts/local_app.py` 同时启动 API 和生产网页，内部端口以预构建 manifest 为准（当前为 18000），浏览器入口为 3000。准备、检查与备份恢复见 [S5 运行手册](../../docs/s5-runbook.md)。产品版本为 0.6.0，业务协议保持 0.5.0。

S3 支持 DeepSeek、OpenAI、Anthropic live，以及默认 mock。模型凭据仅在后端；AI 复盘未经专家校准。同一数据库启动第二实例时 `/readyz` 返回 503 / `runtime_in_use`；不能用进程存活代替业务可用性判断。历史 `routers/sessions.py` 适配器在标准应用返回 410；自动示范使用与人工训练相同的正式持久接口。

新增 `/api/v1/reports/{id}/reviews` 追加人工复核，以及 `/api/v1/calibration` / `export`。数据库升级为 schema v2，完整接口与统计口径以 S4 文档为准。
