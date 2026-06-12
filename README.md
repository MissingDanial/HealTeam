# HealTeam

家庭治疗 AI 陪练系统。项目通过多 Agent 协作模拟家庭治疗会话，帮助心理学学员以更低成本练习评估、干预、观察与复盘能力。

## 项目亮点

- 多 Agent 架构：医生、父亲、母亲、来访者、观察者、督导协同工作
- 前后端分离：`FastAPI` 提供接口与会话流，`Next.js` 提供 Web 界面
- 可扩展编排：基于 `LangGraph` 组织多轮家庭治疗流程
- 测试覆盖基础稳定：当前 `82` 个 `pytest` 用例通过

## 仓库结构

- `healteam/`: 主项目目录，包含后端、前端、数据、测试与文档
- `家庭治疗AI陪练系统-MVP-PRD.md`: 产品需求文档
- `LICENSE`: 仓库开源协议

## 技术栈

- 后端：Python、FastAPI
- 前端：Next.js、TypeScript、Tailwind CSS
- 编排：LangGraph
- 测试：pytest

## 快速开始

进入主项目目录：

```bash
cd healteam
```

常用命令：

```bash
py -m pytest
docker compose up --build
```

更完整的运行说明见 [`healteam/README.md`](./healteam/README.md)。

## 当前进展

- 已完成基础仓库整理、GitHub 上传与首页说明
- 已完成会话 SSE 稳定性优化与相关测试
- 已将本地包引导逻辑抽取为公共模块，便于后续维护

## GitHub 展示建议

- 仓库简介可填写：`家庭治疗 AI 陪练系统，基于 FastAPI + Next.js + LangGraph`
- Website 可填写你后续部署后的演示地址
- Topics 可添加：`fastapi`, `nextjs`, `langgraph`, `multi-agent`, `ai`, `python`, `typescript`

## 许可证

本仓库基于 `MIT` 协议开源，详见 [`LICENSE`](./LICENSE)。
