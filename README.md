# Heart To Heart

这个仓库用于存放“家庭治疗 AI 陪练系统”的实现与相关文档。

## 目录说明

- `healteam/`: 主项目目录，包含后端、前端、数据、测试与文档
- `家庭治疗AI陪练系统-MVP-PRD.md`: 产品需求文档

## 主要技术栈

- 后端: FastAPI + Python
- 前端: Next.js + TypeScript
- 编排: LangGraph
- 测试: pytest

## 快速开始

项目主 README 位于 `healteam/README.md`。

如果你准备直接运行项目，请进入主目录：

```bash
cd healteam
```

然后参考子项目中的说明进行启动、测试和环境配置。

## 上传到 GitHub

建议把当前目录作为 Git 仓库根目录上传，这样可以同时保留：

- 主项目源码
- PRD 文档
- 后续补充的说明文档

上传前请确认：

- 已复制 `healteam/.env.example` 为本地 `.env`
- 没有把真实密钥、缓存文件和构建产物加入版本库
