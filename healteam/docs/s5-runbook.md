# S5 本地运行与数据维护手册

产品版本 0.6.0；业务协议仍为 0.5.0，SQLite 仍为 schema v2。运行环境以 Windows 10/11 x64、Python 3.11/3.12、Node.js 22/24 为目标；本次预构建包由 Node.js 24 生成，需要 Node.js 24 运行。

## 选择交付包

- 本地运行包 `HealTeam-local-0.6.0-win32-x64.zip`：含已构建网页、运行文件清单、源码和维护工具。首次准备只需安装 Python 依赖，不需要 npm 或前端重建。
- 源码包 `HealTeam-S5-2026-11-10.zip`：含源码、锁定依赖、静态病例/流派/量规、测试和文档，首次准备需要安装前端依赖并构建。

两个包都不含系统 Python/Node、私有 `.env`、数据库、备份、控制令牌或本机运行日志。首次安装依赖需要联网；依赖安装完成后，mock 可以离线运行。live 需要配置的模型服务，可能产生费用。

新解压副本没有用户密钥，默认 mock。当前工作区保留之前的 DeepSeek 私有配置，S5 没有更换密钥或发送新的付费模型请求。

## 首次使用

1. 解压到普通本机目录，进入 `HealTeam-main/healteam`。
2. 确认安装了 Python 3.11/3.12 和 Node.js。预构建包应使用 Node.js 24。
3. 双击 `安装与构建.cmd`，等待准备完成。
4. 双击 `开始使用.cmd`。网页打开后保持运行窗口开启。
5. 使用结束，双击 `停止服务.cmd` 或在启动窗口按 Ctrl+C。

网页入口固定默认 `http://127.0.0.1:3000`。当前 managed 构建内部 API 是 18000，因为本环境不能绑定 8000；一键入口按 manifest 使用正确端口。旧的手工开发方式仍可使用 `start_server.py` 与 `npm run dev`，其默认内部端口是 8000。

命令行等价操作（在项目根目录执行）：

```powershell
py -3.12 scripts/prepare_local.py --install --use-prebuilt
.\.venv\Scripts\python.exe scripts/local_app.py doctor --human
.\.venv\Scripts\python.exe scripts/local_app.py start
```

源码包没有预构建文件时，第一条改为 `py -3.12 scripts/prepare_local.py --install`；使用 Python 3.11 时将 `-3.12` 改为 `-3.11`。已安装依赖后只需重建，可运行 `.\.venv\Scripts\python.exe scripts/prepare_local.py`。这些命令不安装系统软件，不读取根密钥来构建网页。

## 启动、停止和状态

启动器前台监督 API 与生产网页服务。API 在监督进程内运行；网页是受 Windows Job Object 管理的子进程。停止或监督进程退出后，只清理本次启动的服务，不根据 PID 文件强杀其他程序。

```powershell
.\.venv\Scripts\python.exe scripts/local_app.py status --human
.\.venv\Scripts\python.exe scripts/local_app.py stop --human
```

重复启动会识别当前副本的既有实例。端口已被其他程序使用时拒绝启动，不接管或结束其他程序。控制通道只绑定本机，使用随机令牌与副本标识；令牌保存在本机状态目录，不进入诊断输出或交付包。

密钥或模型名称缺失时，系统可以启动供历史查看，新生成仍保持不可用；不会自动切成 mock。依赖、运行文件、数据库所有权等问题则需要先处理。

自定义网页端口可用 `local_app.py start --web-port 3001`。更换内部 API 端口必须先停止，再重建：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_local.py --api-port 18001
.\.venv\Scripts\python.exe scripts/local_app.py start
```

启动器校验构建版本、系统平台、Node 主版本、路径和文件 SHA-256。不要手工修改 manifest；更换 Node 主版本或源码版本后应重新准备。校验用于发现本机文件损坏或混用版本，不是发行者身份签名。

`安装与构建.cmd` 检测到已有 manifest 时只验证预构建文件。如果校验失败、网页文件损坏或 Node 主版本更换，需要明确重建：在本目录运行 `.\.venv\Scripts\python.exe scripts/prepare_local.py --install`，不加 `--use-prebuilt`。这一步需要 npm 和联网安装依赖；完成后再启动。

## 环境检查

双击 `运行检查.cmd`，或运行 `local_app.py doctor --human`。检查不发送模型请求，也不初始化或恢复训练数据库。输出只有安全状态、版本、模型模式及处理建议，不包含密钥、模型端点或控制令牌。

| 提示 | 处理 |
|---|---|
| 找不到 Python/Node | 安装项目要求的系统版本，再准备 |
| 缺少依赖或预构建文件 | 运行安装与构建入口 |
| Node/项目版本不符或文件哈希不符 | 停止服务后按上文命令明确重建，不手工改 manifest |
| 本机端口被占用/系统不允许绑定 | 使用空闲网页端口，或按目标 API 端口重新构建 |
| 另一实例持有数据库 | 停止对应副本后重试，不删除锁文件 |
| 模型配置未完成 | 检查私有 `.env`，重启后端；历史仍可查看 |

状态和网页日志在 `.cache/local-app`；不应把整个私有目录当作公开交付材料。反馈问题时可先提供检查摘要与安全错误码。

## 完整备份

备份包含完整对话、报告、幂等收据、人工复核姓名和备注。它应私下保管；校准页面的去标识化 JSON 不能代替数据库备份。

停止服务后双击 `备份数据.cmd`。默认输出到 `backups`，不覆盖已有同名文件。命令行可显式指定数据库和位置：

```powershell
.\.venv\Scripts\python.exe scripts/manage_data.py backup --output backups/my-records.zip --human
.\.venv\Scripts\python.exe scripts/manage_data.py verify backups/my-records.zip --human
```

工具持有数据库 RuntimeLease，使用 SQLite 快照 API，归档只有 manifest 与数据库。校验包括格式、大小上限、SHA-256、schema v1/v2、SQLite 完整性和外键，以及会话、事件、收据和人工修订的引用/哈希。拒绝异常数据库对象、未知版本及不受支持的归档。

单个数据库上限为 512 MiB；达到上限时操作失败，不裁剪记录。备份不包含私有 `.env`，也不自动删除旧备份。

## 恢复与回退

先停止服务，验证备份，再恢复。默认只写入不存在的目标；显式覆盖已有库时会先生成并校验旧库安全 ZIP，再替换。

```powershell
.\.venv\Scripts\python.exe scripts/manage_data.py restore backups/my-records.zip --database data/recovered.sqlite3 --human
# 确实需要替换配置中的现有库时：
.\.venv\Scripts\python.exe scripts/manage_data.py restore backups/my-records.zip --replace --human
```

失败时按输出安全码处理，不手工删除 SQLite 的 WAL/SHM/日志文件。旧库损坏到无法独立验证时，工具不会直接覆盖它；应恢复到新文件，并把私有 `.env` 中的 `HEALTEAM_DATABASE_URL` 指向新库。旧库和安全副本保留供核对。

恢复不会调用模型或自动迁移。schema v1 备份恢复后，首次启动新服务按 S4 规则升级为 v2。旧代码不能打开新 schema；回退软件时使用升级前备份。

正常停机后等待输入的会话可继续；中断半轮不会自动补发，复盘中断按已有规则重试。恢复后的报告、修订和请求 ID 保持原样，不把恢复当作一次新生成。

## 交付构建

```powershell
.\.venv\Scripts\python.exe scripts/package_source.py --verify-run --output ..\..\output\HealTeam-S5-2026-11-10.zip
.\.venv\Scripts\python.exe scripts/package_local.py
```

源码包排除 `.local`、备份、日志、数据库和密钥；运行包只额外加入已验证的 `.local/runtime`。输出 ZIP 必须放在源码目录之外，避免把输出自身或旧备份混入。每包附 SHA-256；根静态资源与运行文件清单都会验证。

## 容器边界

Docker 仅做静态修正，本环境没有 Docker，未实际构建运行。新记录卷为 `healteam_records:/var/lib/healteam`，静态病例和量规保留在镜像 `/opt/healteam/data`；健康检查使用 `/readyz`。

旧 `healteam_data` 卷不自动迁移或删除。旧容器用户应先在旧版本停止并备份数据库，再在新记录路径恢复；不要用删除卷的命令代替迁移。没有把容器配置检查当作本次 Windows 运行验收。

S5 是单人本机交付，未增加多人登录、机构权限或真实专家校准。原试行量规、AI 复盘与本机复核的有效性边界保持不变。
