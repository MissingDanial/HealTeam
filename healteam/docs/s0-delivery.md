# S0 交付与验收记录

验收日期：2026 年 11 月 4 日（Asia/Shanghai）。本轮目标为基线可运行、病例与会话契约明确，并修复进入下一阶段前的关键正确性问题。

## 开发位置

开发在当前工作区的 `HealTeam-main/healteam` 副本完成，未写入桌面原项目。启动与后续开发请使用本副本。

## 已完成

1. 建立可安装的根 Python 项目和固定运行依赖，修复本地包导入与资源定位。
2. 统一 `HEALTEAM_*` 配置；默认离线 mock，明确真实调用的供应商、模型、密钥、超时和输出预算。
3. 病例使用唯一强模型，显式迁移旧字段，内容进入真实角色输入；原病例 JSON 内容未改。
4. 修复第零轮重复问题；10、20、30 回合均具有一致编号；明确设置图执行预算，避免干净环境默认步数不足。
5. 家庭输入过滤私密提示与内部事件；观察者直接读取选定流派，并排除医生提醒造成的家庭行为误报。
6. API 在开始演示前校验内容和参数；配置或资源错误可诊断；不向浏览器及日志暴露密钥和内部报告路径。
7. 正式首页、同源 API 代理和临时演示入口贯通；修复演示页的跨网络块 SSE 解析、失败状态及提示文本转义。
8. 提供未来训练请求、会话快照、事件与状态迁移模型、JSON Schema 和有效样例；阶段不能倒退，取消后不能写完成，终态不可修改。
9. 更新启动、配置、契约与本阶段能力边界说明。

## 实际验收结果

| 项目 | 结果 |
|---|---|
| Python 3.12 测试 | 182 passed，0 failed |
| 测试分组 | API 27、病例 74、契约 21、集成 24、提示词 36 |
| 原始测试基线 | 82 项通过；本轮新增 100 项检查 |
| 离线 smoke | 导入、三流派、接口预检、20 回合通过 |
| 契约导出同步检查 | 通过 |
| Next 生产构建 | 通过 |
| TypeScript 检查 | 通过 |
| 前端 lint | 通过 |
| 干净 Python 环境 | 运行锁文件安装、editable 安装与依赖兼容检查通过 |
| 标准 wheel | 构建和独立目录导入通过；内容资源通过 HEALTEAM_ROOT 提供 |
| 真实浏览器 | 7 项检查通过，页面脚本错误为 0 |
| 测试服务清理 | API、Next 和浏览器均已关闭 |

浏览器检查覆盖：首页、同源病例接口、三轮萨提亚离线演示、报告标识、单字节中文 SSE 分块、提示文本安全及 HTTP 失败状态。机器记录见 [s0-verification.json](s0-verification.json)。

测试过程存在一条 LangGraph 依赖的未来默认值变更提示，不影响上述通过结果。没有调用外部模型。

## 明确边界

当前仍是 demo 专用 POST SSE 接口。S0 的新训练模型和样例不代表训练接口已经实现。学员输入、SQLite、持久事件与报告查询按 S1 及后续阶段建设。

旧演示线程在浏览器断开后仍可能继续执行；本轮仅修复订阅等待任务清理，没有宣称完成会话取消。HTTP 适配器使用请求级客户端过渡，统一异步调用链留待阶段计划完成。

Docker 配置已整理，但本轮未构建和运行容器。外部真实模型连通性、反馈专业性和教学有效性未验收。

## 复验方式

从 `healteam` 目录按 README 安装，然后执行：

```powershell
.\.venv\Scripts\python.exe scripts/smoke.py
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe -m pytest -q
```

前端在 `apps/web` 运行 `npm run build`、`npm run typecheck`、`npm run lint`。

浏览器可选依赖通过 `pip install -e ".[browser]"` 安装。在 API 与 Next 均为 mock 模式运行时，执行 `python scripts/browser_smoke.py`；Windows 默认使用已安装的 Edge，其他系统可安装 Playwright Chromium 或指定 `--channel`。

## S1 开发入口

依据 [s0-contracts.md](s0-contracts.md) 接入单人会话管理和 SQLite，再完成学员输入、家庭回应与明确标识的离线流程报告。优先建立一条可操作、可保存的纵向训练流程。
