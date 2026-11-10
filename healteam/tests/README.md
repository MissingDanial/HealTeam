# HealTeam 测试

从项目根目录运行 `.venv/Scripts/python.exe -m pytest -q`。S5 全套回归与启动器修复复测按测试标识合并，共 588 项通过、3 项跳过、0 项待解决失败，包含 106 项本地运维与交付测试。跳过项为 Windows 不支持的 POSIX fork，以及当前环境禁止创建目录链接的两项测试。测试使用 mock、本地 fixture 和临时数据库，不调用真实模型。

数据库测试覆盖独立进程重新打开、幂等收据、版本保护、争用超时、失权回滚和取消竞态；服务与 API 测试覆盖人工输入、回合交接、生成超时、迟到写入隔离、中断复盘重试与持久事件。`tests/reliability` 使用真实 HTTP 和强制关闭子进程，覆盖回应/等待/报告阶段恢复、断开事件流，以及 30 轮共 120 次重复请求的有界并发检验。

`scripts/smoke.py` 验证离线最短闭环；`scripts/export_contracts.py --check` 验证 Schema 未漂移。正式前后端启动并使用独立测试数据库后，`scripts/browser_training_smoke.py` 验证浏览器完整流程，`scripts/browser_reliability_smoke.py` 验证网络与恢复故障。历史 `browser_smoke.py` 对应 S0 页面。

验收结果和未覆盖范围见 [S5 交付记录](../docs/s5-delivery.md)。

S3 新增 providers 双协议本地 fixture、API 三供应商×三流派完整闭环、角色/证据校验、持久预算回归。`scripts/browser_live_smoke.py` 必须核对本地 fixture 专用模型，自动测试不使用真实密钥。真实 DeepSeek 短连接验证单独记录于交付文档。

S4 新增 schema v1→v2迁移、追加复核修订/幂等/版本冲突、证据归属、模型观察量规和统计分母回归。参考样例为合成软件测试数据，不是专家标注；`scripts/browser_evaluation_smoke.py` 仅使用本地模型fixture。

S5 新增完整备份/校验/恢复与并发替换保护、旧根目录继承下的搬移维护、预构建文件清单与密钥隔离、真实 Windows 进程退出、生产运行包重启、源码排除规则，以及训练与人工复核恢复后的幂等重放。运行 Windows 生产生命周期测试前，请先停止占用 18000/3000 的本项目服务。
