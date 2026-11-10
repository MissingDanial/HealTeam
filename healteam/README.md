# HealTeam

家庭治疗模拟与练习系统。当前版本 **0.6.0 / S5 本地交付与运维**：提供 Windows 一键启动、环境检查、预构建网页运行包，以及完整训练记录的备份、验证和恢复。保留 S4 的模型训练、试行量规和人工复核。

## 现在可以做什么

- 选择拒学病例、三个流派之一，以及亲自练习或自动演示。
- 亲自练习时由学员担任治疗师，AI 医生不会代替学员发言。
- 每次输入后收到一至三个家庭回应，再继续下一轮。
- 完成一轮后主动结束并复盘，或者达到轮数上限后自动结束。
- 在本机 SQLite 保存对话、事件、输入收据与报告，刷新和服务重启后重新查看。
- 取消进一步生成；等待输入的会话可以在服务重启后继续，运行中断的半轮不会自动重跑。
- 生成卡住时按预算退出；迟到回应不能覆盖已经取消或重试的结果。
- 断网和刷新时在当前标签页保留草稿及待确认请求，核对后重试同一条干预。
- 中断的复盘可单独重试；同一数据库的第二个后端实例不会误恢复首实例的会话。

**live 模式使用配置的外部模型，发送虚构病例角色资料及会话对话，可能产生费用。AI 复盘未经专家校准，不进行专业能力评分或治疗效果判断。mock 模式使用固定模板。**

S4 新会话保存版本化试行量规。live 复盘按对话参与、探索表达、流派一致、合作下一步给出 0/1/2 或证据不足，不生成总分。每项引用真实发言，显示实际参考范围；人工复核保留每次修订，原 AI 报告不改。量规未经专家校准，复核者身份由本机填报、未经系统认证。

## 一键本地使用

Windows x64 安装 Python 3.11/3.12 和 Node.js 后，在本目录操作。当前预构建运行包由 Node.js 24 构建，运行时需要 Node.js 24；其他版本可从源码重新构建。

1. 首次使用双击 `安装与构建.cmd`。预构建包只安装 Python 锁定依赖并验证网页文件；源包会安装前端依赖并构建。首次安装依赖需要联网，不会安装或更改系统 Python/Node。
2. 双击 `开始使用.cmd`，等待网页打开。运行窗口保持开启，网页地址为 http://127.0.0.1:3000 。
3. 结束时双击 `停止服务.cmd`，或在启动窗口按 Ctrl+C。启动器只停止自己启动的服务，不按其他进程的 PID 强制结束。
4. 有问题时双击 `运行检查.cmd`，它不会调用付费模型或改写训练库。

S5 预构建的一键版内部 API 为 `127.0.0.1:18000`，启动器按 `.local/runtime/manifest.json` 使用已构建配置。当前环境不允许绑定 8000；网页入口仍是 3000。需要其他 API 端口时，停止服务后运行 `python scripts/prepare_local.py --api-port 新端口` 重建，不能只改端口环境变量。

预构建文件会检查版本、平台、Node 主版本及 SHA-256；检查不通过时请重新准备，不要手工修改 manifest。密钥只在 API 私有配置中；网页构建与 Node 子进程不接收模型凭据。

文件校验失败或更换 Node 主版本时，停止服务后在本目录运行 `.\.venv\Scripts\python.exe scripts/prepare_local.py --install`，明确重新构建；此时需要 npm 和网络。已有预构建文件时，双击安装入口只会再次验证，不能修复损坏的网页文件。

## 备份与恢复

先停止服务，再双击 `备份数据.cmd`。完整备份保存在 `backups`，包含对话、报告、输入收据及人工复核姓名/备注；它是敏感数据备份，不等同于校准页的去标识化导出。

把备份 ZIP 拖到 `验证备份.cmd` 上可以检查完整性。恢复到不存在的目标时，可将 ZIP 拖到 `恢复数据.cmd`；默认读取本机配置的数据库路径，不覆盖现有库。

需要替换现有库时明确执行 `恢复数据.cmd "备份文件.zip" --replace`。工具先检查备份并生成旧库的可验证安全备份，再原子替换；验证失败或仍有服务持有数据库时不替换。无法验证的损坏旧库不会直接覆盖，可用下列命令恢复到新文件，再将私有 `.env` 的 `HEALTEAM_DATABASE_URL` 指向它：

```powershell
.\.venv\Scripts\python.exe scripts/manage_data.py restore "备份文件.zip" --database data/healteam-recovered.sqlite3 --human
```

恢复不调用模型，也不自动迁移数据版本；首次启动服务会按已有迁移约定处理。完整操作与故障说明见 [S5 运行手册](docs/s5-runbook.md)。

## 手工开发启动

项目内已有 `.venv` 和前端依赖时，可直接启动。本轮工作区副本已配置 DeepSeek / deepseek-flash；凭据只在私有 `.env`，不随源码包交付。

终端一，在本目录 `healteam` 运行：

```powershell
.\.venv\Scripts\python.exe scripts/start_server.py
```

终端二：

```powershell
cd apps/web
npm run dev
```

打开 http://127.0.0.1:3000 。选择“亲自练习”，提交一次干预，等待家庭回应。完成一轮后可以点击“结束练习并复盘”。首页“最近的会话”可以继续未结束的练习或重开已保存的报告。

前后端分别在 127.0.0.1:8000 和 127.0.0.1:3000。默认只供本机使用。

新会话完成后，复盘页可查看试行量规并展开“本机人工复核”：填写姓名、分项判断和证据后保存；修改会追加新修订。首页入口可打开 `/calibration` 查看统计与下载去标识化评审数据。旧 S3 报告不追补 AI 评级，需新练习才能生成新版量规。

校准只统计最新人工修订；AI 或人工无法判断、人工证据超出 AI 参考范围的维度不进入精确一致率。mock、自动示范和旧版报告不进入学员 AI 校准配对。统计始终显示待校准，不代表专家认证或临床有效性。

## 首次安装

Python 3.11 或 3.12，Node.js 22 或 24；本轮实际验收使用 Python 3.12 与 Node.js 24。

PowerShell，在 `healteam` 目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
cd apps/web
npm ci
```

Python 启动器未登记 3.12 时，可使用对应解释器的完整路径。uv 用户可以使用 `uv venv --python 3.12 .venv`，再用 `uv pip install --python .venv/Scripts/python.exe -r requirements.lock` 与 `uv pip install --python .venv/Scripts/python.exe --no-deps -e .`。

开发测试依赖：`python -m pip install -e ".[dev]" -c requirements.lock`；可选浏览器依赖：`python -m pip install -e ".[browser]"`。这里的 python 指项目 `.venv` 中的解释器。

## 数据保存与配置

默认数据库为 `data/healteam.sqlite3`，启动时自动初始化 schema v2。可在项目根 `.env` 设置 `HEALTEAM_DATABASE_URL`，值为本地文件路径或 `sqlite+aiosqlite:///...` URL。不使用内存数据库或 PostgreSQL。S1–S3 的 schema v1 会在事务中升级到 v2，仅新增人工复核表；原会话、事件、收据和报告保留。升级前先停止后端并备份数据库；旧版本不能直接打开已升级的 v2，回退应使用升级前备份。

根 `.env` 由后端启动脚本加载。新解压源码使用 `.env.example` 默认 mock。切换 live 时设置 `HEALTEAM_MODEL_MODE=live`、`HEALTEAM_LLM_PROVIDER`、`HEALTEAM_LLM_API_KEY`、`HEALTEAM_LLM_MODEL`，以及需要时的 `HEALTEAM_LLM_BASE_URL`，然后重启后端。

DeepSeek 示例：provider 为 `deepseek`，base URL 为 `https://api.deepseek.com/v1`，model 为 `deepseek-flash`。OpenAI 使用 Chat Completions，Anthropic 使用 Messages；模型名显式配置，不自动替换。密钥不从浏览器输入，不保存在训练数据库。

每会话默认最多 128 次模型调用，token 预算 250000；可用 `HEALTEAM_MODEL_MAX_CALLS`、`HEALTEAM_MODEL_TOKEN_BUDGET` 调整。调用前持久预留，超时/取消的未知用量不当成零；统计只用于本地预算，不代替账单。`HEALTEAM_CONTEXT_MAX_CHARS=30000` 限制上下文，`HEALTEAM_LLM_MAX_TOKENS=1024` 限制家庭输出；新量规复盘独立使用 `HEALTEAM_REVIEW_MAX_TOKENS=4096`，并计入同一会话预算。旧会话保留原输出限制。

live 失败不会自动切回 mock，也不自动重发模型请求。报告失败可显式重试，只重做复盘且继续累计用量。历史会话固定其模式、provider、model、端点指纹和提示词资源；更换服务/模型后旧 live 会话仍可查看、取消，但继续生成需要恢复匹配配置。只换密钥可以继续。

运行预算可设置 `HEALTEAM_OPERATION_TIMEOUT_SECONDS=60`（每次生成/监督步骤）、`HEALTEAM_STOP_TIMEOUT_SECONDS=2`（停止任务等待）、`HEALTEAM_STORAGE_BUSY_TIMEOUT_SECONDS=2`（每次 SQLite 写锁等待）。这些不是整场训练的总时长；数据库操作有独立等待预算。

同一规范数据库路径只允许一个后端实例执行。数据库旁的 `.lock` 文件由操作系统加锁；文件留存是正常现象，进程退出会自动释放锁，不需要手动删除。第二个实例保持诊断入口可用，`/readyz` 返回 `runtime_in_use`；停止重复实例即可。

前端通过同源 `/api/v1` 代理连接后端。修改后端端口时，需在前端启动或构建终端设置对应地址，例如 `$env:HEALTEAM_API_INTERNAL_URL="http://127.0.0.1:8001"`。密钥和数据库地址不进入浏览器。

手动初始化数据库可用 `python scripts/migrate_database.py --database data/healteam.sqlite3`。未知数据库版本、未标版本且已有其他数据的文件会被拒绝，不会自动改作训练库。

备份训练记录时先停止服务，再使用 `备份数据.cmd` 或 `scripts/manage_data.py backup` 生成完整 ZIP，并用 `verify` 验证；不要只复制正在使用的 SQLite 主文件。源码包、Git 忽略规则及 Docker 构建上下文均排除本地数据库、缓存和生成报告。

## 正式接口

- `POST /api/v1/sessions`：201 创建稳定会话；相同创建请求重放返回 200。
- `GET /api/v1/sessions`：本机历史与分页。
- `GET /api/v1/sessions/{id}`：状态、进度与对话快照。
- `GET /api/v1/sessions/{id}/events`：重放并订阅持久事件。
- `POST /api/v1/sessions/{id}/messages`：幂等接受学员输入。
- `POST /api/v1/sessions/{id}/finish`、`cancel`：结束复盘或取消。
- `GET /api/v1/reports/{id}`：报告状态和已保存内容。
- `POST /api/v1/reports/{id}/retry`：只重试失败的复盘，不重跑对话。
- `GET/POST /api/v1/reports/{id}/reviews`：读取复核历史或追加幂等修订。
- `GET /api/v1/calibration`、`export`：分组一致率与不含姓名/备注/对话的评审数据。

旧 `/index.html` 已迁移到首页；旧 `/api/v1/demo/sessions` 在标准应用返回 410，避免绕过单活跃会话约束。自动示范现在使用与训练相同的持久资源协议。S0 报告文件不自动导入 S1 数据库。

## 验证

```powershell
.\.venv\Scripts\python.exe scripts/smoke.py
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe -m pytest -q
cd apps/web
npm run build
npm run typecheck
npm run lint
```

`smoke.py` 强制离线验证基础内容、20 回合编号、真实学员输入和跨应用报告重开，使用临时数据库，不写入用户训练记录。

API 与 Next 启动后，可以使用独立测试数据库运行 `python scripts/browser_training_smoke.py` 和 `python scripts/browser_reliability_smoke.py`。Windows 默认使用已安装的 Edge；其他平台可准备 Playwright Chromium 或指定 `--channel`。浏览器脚本会创建并取消/结束测试会话，应使用测试数据库。故障脚本的中断复盘场景需要 `--report-session-id`，详见 `--help` 和 S2 验收记录。

`tests/reliability` 会在随机本机端口启动自己的测试子进程并强制结束，验证崩溃与重启；使用临时数据库。全套测试不写入默认用户数据库。

打包运行 `python scripts/package_source.py --verify-run`。脚本保留 `data/cases`、`data/schools` 等静态资源，排除本地训练数据与密钥，并解压后运行离线冒烟检查。

Docker 配置已把可写记录卷移到 `/var/lib/healteam`，避免覆盖 `/opt/healteam/data` 下的版本化病例与量规，并使用 `/readyz` 健康检查。当前环境无 Docker，容器构建运行仍未实测；旧数据卷不会自动删除或迁移。

## 开发资料

- [S5 运行手册](docs/s5-runbook.md)
- [S4 评估与复核约定](docs/s4-contracts.md)
- [S3 模型与用量约定](docs/s3-contracts.md)
- [机器可读 Schema 与样例](docs/contracts/)
- [S5 交付与验收记录](docs/s5-delivery.md)
- [S4 历史交付记录](docs/s4-delivery.md)
- [S3 历史交付记录](docs/s3-delivery.md)
- [S2 历史交付记录](docs/s2-delivery.md)
- [S1 历史交付记录](docs/s1-delivery.md)
- [S0 历史验收记录](docs/s0-delivery.md)

历史 W1/S0/S1 文档保留作演进参考，当前业务行为以 S4 规格为准，本地运行方式以本 README 和 S5 手册为准。

S3 本地 live 浏览器验收见 `scripts/browser_live_smoke.py --help`；脚本要求专用本地 fixture 和模型名，不可直接对付费服务运行自动验收。

S4 浏览器验收脚本为 `scripts/browser_evaluation_smoke.py`，同样要求专用本地模型 fixture。合成评估基准位于 `tests/fixtures/evaluation-reference-cases.json`，不是专家标注数据。

制作源码包：`python scripts/package_source.py --verify-run`。制作预构建运行包：`python scripts/package_local.py`。两个包都排除私有配置、数据库、备份和运行状态；运行包额外包含已校验网页文件。
