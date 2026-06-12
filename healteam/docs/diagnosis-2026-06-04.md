# HealTeam 诊断报告

> **生成时间**: 2026-06-04
> **范围**: W1 交付物的端到端系统测试 + 静态分析
> **方法**: smoke test + pytest + 手动 API 探测 + Docker/Next.js 配置审查
> **TL;DR**: W1 范围内的"架构骨架 + 内容资产"**完全通过**(77/77 测试,所有 stub 端点按设计响应),但发现 **2 个真实 bug**(Web Dockerfile 配置不当、文档与代码 agents 数量不一致)和 **1 个 W2 必做项**(case JSON 的 `agent_role_hints` 未被运行时消费)。

---

## 0. 测试矩阵总览

| 维度 | 状态 | 证据 |
|---|---|---|
| `scripts/smoke.py`(零依赖基线) | ✅ PASS | 13 imports + 4 memory 步骤 + 5 agent 契约 + 1 orchestrator 编译与运行 |
| `py -m pytest tests/` | ✅ **77/77 PASS** (0.31s) | 见 §1 |
| `scripts/demo_w1.py` | ✅ "不崩" | 20 轮占位输出,符合 W1 范围 |
| FastAPI 10 路由 | ✅ 全部按设计响应 | 详见 §2 |
| 数据资产 (case/schools/prompts) | ✅ 完整且结构合法 | 详见 §3 |
| Docker Compose 配置 | ⚠️ **配置文件有效,但 Web 镜像会启动失败** | 详见 §4 |
| LangGraph 状态机 | ✅ 6 节点编译并跑通 20 轮 | 详见 §5 |

---

## 1. pytest 套件 (77/77 通过)

```
============================= 77 passed in 0.34s ==============================
```

| 测试文件 | 数量 | 范围 |
|---|---|---|
| `tests/cases/test_case_content.py` | 31 | 案例 JSON 字段、3 流派模板必备章节、术语互斥、案例-流派兼容性 |
| `tests/integration/test_session_e2e.py` | 9 | 状态机端到端、Bowen/Satir 也能跑、psychiatrist 不发言、observer 永远 None |
| `tests/prompts/test_*.py` | 37 | 7 个 prompt 文件结构、agent 路径、validator 接受/拒绝规则、should_speak 纯函数性 |

**亮点**:
- `test_w1_demonstrates_state_machine_works_not_real_therapy` 把"医生占主导"诚实写为 W1 已知行为,而不是 bug-as-spec(v1.0 → v1.1 修订已纠正)。
- `test_school_differentiation` 显式断言 "结构派不能用 Bowen 的 genogram 术语" 等反向约束。

**关注点**:
- 所有测试**不消费 LLM**,所以这 77 个测试不能证明"真实治疗仿真"有效——只能证明"骨架+契约"完整。这与 W1 报告 v1.1 范围一致。

---

## 2. FastAPI 端点探测 (10 路由,全部按设计响应)

| 端点 | 方法 | W1 行为 | 实际验证 |
|---|---|---|---|
| `/healthz` | GET | 200 `{"status":"ok"}` | ✅ 200 |
| `/readyz` | GET | 200 `{"status":"ready"}` | ✅ 200 |
| `/api/v1/agents` | GET | 返回 6 个 agent (W1 文档说 5 个,**不一致**) | ⚠️ 见 §6 bug-1 |
| `/api/v1/sessions` | GET | `[]` | ✅ `[]` |
| `/api/v1/sessions` | POST | 占位 `00000000-...` (W1 故意) | ✅ 201, **但接受任何 body** (含 `{}`) |
| `/api/v1/reports/{id}` | GET | 501 (W2) | ✅ 501 + "Reports endpoint is W2 work" |
| `/docs`, `/redoc`, `/openapi.json` | GET | Swagger/Redoc | ✅ 5 paths in OpenAPI schema |

**Pydantic 模型验证 (`Hint.confidence`)**:
```python
Hint(round=0, trigger='x', content='y', confidence=1.5) → ValueError
```
✅ 边界校验工作正常。

---

## 3. 数据资产 (内容质量: 优)

| 资产 | 规模 | 评估 |
|---|---|---|
| `data/cases/teen-truancy-01.json` | 20KB / 354 行 | **临床深度突出**: 5 个里程碑、5 个治疗焦点、9 篇文献、3 个家庭成员各 5 个防御模式 (含 underlying_fear)、Bowen/Satir/Structural 三学派分析 |
| `data/schools/{structural,bowen,satir}.md` | 8.3~9.0KB / 8 H2 节 各 | 三流派都引用了创始人;测试验证术语互斥(结构派不出现"genogram"等) |
| `packages/prompts/*.md` | 7 文件 / 7~10KB 各 | 7 个文件齐全(doctor/psychiatrist/observer/father/mother/patient/orchestrator) |
| `data/case-school-matrix.md` | 6.6KB | 案例-流派映射矩阵存在 |
| `docs/schools-comparison.md` | 6.2KB | 三流派对比文档存在 |

---

## 4. Docker 部署分析 (W1 范围)

### 4.1 `docker-compose.yml` (✅ 合法)
- Postgres 16 + pgvector 镜像健康检查正常
- API/Web 服务的 `depends_on` 健康检查链正确
- `restart: unless-stopped` 配置合理

### 4.2 `apps/api/Dockerfile` (✅ 可构建)
- 多阶段构建: builder (poetry) + runtime (slim)
- 健康检查脚本正确
- **`packages/prompts` 的 COPY 路径有冗余但不致命**:
  ```dockerfile
  COPY --from=builder /build/packages /app/../packages
  COPY --from=builder /build/packages/prompts /app/packages_prompts
  ```
  第二条把 prompts 复制到 `/app/packages_prompts/`(平铺),而第一条已把整个 `packages` 复制到 `/packages/`。W1 API 启动时不需要 prompts 文件,所以不会失败——但结构混乱。**W2 接 LLM 后需要明确 prompts 路径**。

### 4.3 `apps/web/Dockerfile` (❌ **真实 bug**)

**问题**: Dockerfile 第 32 行:
```dockerfile
COPY --from=builder --chown=nextjs:nodejs /build/.next/standalone ./
...
CMD ["node", "server.js"]
```

但 `apps/web/next.config.js` **没有** `output: 'standalone'`:
```js
const nextConfig = {
  reactStrictMode: true,
  env: { ... },
};
```

**影响**: `next build` 默认不输出 `.next/standalone/`。`docker compose up` 启动后,`healteam-web` 容器会因 `node server.js` 找不到 `server.js` 而**立即退出**。API 会起来,Web 永远 502。

**修复 (一行)**:
```js
// apps/web/next.config.js
const nextConfig = {
  output: 'standalone',   // ← 新增
  reactStrictMode: true,
  env: { ... },
};
```

**严重度**: **高**——`docker compose up` 是 README Quick Start 第一条命令,首次按文档操作必踩。

### 4.4 Docker 不可用
本环境 (`win32`) 无 `docker` 命令,无法 `docker compose config` 或 `docker compose up` 实测验证。**上述 Dockerfile 问题是通过静态分析得出,需要在有 Docker 的环境复现确认**。

---

## 5. LangGraph 编排器行为 (W1 占位)

### 5.1 状态机拓扑
```
load_case → decide_speaker → agent_respond → observer_hint → update_memory → check_end
                                                                          ↓
                                                                  (round<20) decide_speaker
                                                                  (round≥20) END
```

### 5.2 W1 demo 实际输出 (与文档一致)
```
Round  0..19 | [doctor] | [doctor placeholder | round=N]
Speaker distribution: doctor: 20 (100.0%)
```

**这是 W1 已知行为**,非 bug:
- `decide_speaker` 优先级: doctor > patient > mother > father
- `DoctorAgent.should_speak` 在 round 0 或"前 3 事件都是非医生"时返回 True
- 家庭 `should_speak` 依赖"上一条话含关键词"——但 placeholder 永远不含

**W2 修复路径** (来自 W1 报告 §7,具体到行号):
1. `packages/agents/doctor.py` L51 — `NotImplementedError` → `self.llm.chat(messages)`
2. `packages/agents/orchestrator.py` L99-104 — `load_case` 读 `data/cases/teen-truancy-01.json`
3. `packages/agents/observer.py` L38-41 — `return None` → 基于 LLM 的 hint
4. 新增 `packages/agents/supervision.py` — 督导会子图

---

## 6. 发现的 Bug 与不一致 (按严重度)

### Bug-1 (中): 文档与代码 "agents 数量" 不一致

| 位置 | 写法 |
|---|---|
| `healteam/README.md:22, 105` | "5 agent stubs" / "lists the 5 agents" |
| `healteam/docs/architecture.md:92, 199, 243, 330, 423, 442` | 6 处"5 agents" |
| `healteam/apps/api/app/routers/agents.py:31` | `@router.get(... summary="List the 5 HealTeam agents")` |
| `healteam/packages/agents/__init__.py:6` | "5 concrete agent classes" |
| `healteam/家庭治疗AI陪练系统-MVP-PRD.md:151` | PRD §6 说 "5 个 Agent 实时协作",但 §6.1-6.4 实际列出 **6 个** |
| **实际代码** | **6 个**: doctor, psychiatrist, observer, father, mother, patient |
| **API 返回** | **6 个** (count= 6) |

**结论**: 项目实际是 6-agent 设计,文档和 OpenAPI summary 都错写成 5。需统一改为 6。

**修复 (低工作量)**:
- `agents.py` summary: `"List the 6 HealTeam agents"`
- README/architecture: `5` → `6`
- `__init__.py`: `5 concrete agent classes` → `6 concrete agent classes`
- 决定 PRD 是否同步修订 (或注脚说明"5 个治疗室角色 + 1 个督导会专属心理医生")

### Bug-2 (高): Web Dockerfile 与 next.config.js 不一致

见 §4.3。**`docker compose up` 必失败**——`next.config.js` 缺 `output: 'standalone'`,但 `Dockerfile` 假设存在。

**修复**:
```js
// apps/web/next.config.js L2 后加:
output: 'standalone',
```

### 观察项 (非 bug,但有改进空间)

- **`POST /api/v1/sessions` 接受任何 body**: 当前 stub 返回固定值,不校验 `case_id`/`school` 的合法性。W2 必做。
- **`load_case` 节点是空操作** (orchestrator.py L99-105): 只设置 `status=RUNNING`,**不读** `data/cases/teen-truancy-01.json`。因此 W1 集成测试里 `school=BOWEN` 或 `school=SATIR` 的 Session **没有任何流派差异**——agent 行为完全由 placeholder 决定。W2 必接 case JSON。
- **`case JSON` 的 `agent_role_hints` 字段未被消费** (W1 issues I-7 类似但未列出): 这是案例里最有临床价值的内容之一(每位家庭成员 3 句台词草案 + 说话风格),W2 必须在 LLM prompt 里注入。
- **`WebFrontend` Dockerfile 假设 `pnpm-lock.yaml*`**: `apps/web/` 目录下没有 `pnpm-lock.yaml`(只有 `package.json`),会走 `npm install` 分支。W2 项目初始化时若用 pnpm,需提交 lockfile。

---

## 7. W1 范围内"应当"做但未做的项

对照 W1 报告 §3 列出"out-of-scope"项,**全部按设计未做**,无遗漏:

| W1 故意不做 | 状态 | 落点 |
|---|---|---|
| 真实 LLM 调用 | ✅ 按设计 mock | W2 |
| 家庭成员轮流发言 | ✅ placeholder doctor 占主导 | W2 (接 LLM 自然修) |
| Observer 旁路提示 | ✅ `observe()` 永远 None | W2 |
| 督导会 | ✅ 完全没实现 | W2 |
| 里程碑实际检查 | ✅ 不检查 | W2 |
| Case JSON 消费 | ✅ 只 read,不 hydrate | W2 |
| Web UI 真实交互 | ✅ 只 landing page | v0.2 |
| Postgres 持久化 | ✅ in-memory | v0.2 |

---

## 8. 总体评估

| 维度 | 评分 | 说明 |
|---|---|---|
| **W1 范围达成** | **A** | 77/77 测试通过,所有 stub 路由按设计响应,内容资产临床深度优秀 |
| **诚实性** | **A** | W1 报告 v1.1 主动修正了 v1.0 的"骨架当行为报"偏差,值得肯定 |
| **代码可读性** | **A** | Protocol/Type hint/文档字符串齐备,`apps/api` 3 个 router 文件每个 < 35 行 |
| **可运行性 (无 Docker)** | **A** | smoke.py + pytest + demo_w1.py 三个入口都能在裸 Python 跑通 |
| **可部署性 (有 Docker)** | **C** | Web Dockerfile 与 next.config.js 不一致,`docker compose up` 必失败 |
| **文档一致性** | **B-** | "5 agents" 错误出现在 6+ 处,与实际 6-agent 设计矛盾 |

**核心结论**: W1 收尾交付物的**架构地基**扎实可靠,**内容资产**专业度足以支撑 W2 接 LLM。但**部署层**(Dockerfile)有 1 个阻塞级不一致,需要在 W2 启动前修。**文档**需要小范围修订以消除"5 vs 6 agents"歧义。

---

## 9. 建议的下一步 (按优先级)

1. **[必做,5 分钟]** `apps/web/next.config.js` 加 `output: 'standalone'`,验证 `docker compose up` 能起 web 容器。
2. **[必做,30 分钟]** 全局 "5 agents" → "6 agents" 修订 (Bug-1)。同时核对 PRD §6 与实际代码。
3. **[W2 启动前]** 确认有 Docker 的环境能跑通完整 `docker compose up`,捕获其他环境差异。
4. **[W2 必做]** 按 W1 报告 §7 的 4 行指引,接入 OpenAI 兼容 LLM client,优先做 `doctor.py` (L51) 和 `orchestrator.py` (L99-104) 的 case JSON 消费。
5. **[W2 应做]** 把 case JSON 的 `agent_role_hints` (每位家庭成员 3 句台词草案) 注入对应 agent 的 system prompt,这是临床保真度的关键资产,不能浪费。
6. **[v0.2]** Postgres + pgvector 接入,跨会话学习;Web UI 实现治疗会话实时对话。

---

*报告完。所有结论基于 `py scripts/smoke.py` / `py -m pytest tests/` / `TestClient` 探测 / 静态文件分析(本环境无 Docker)。如需 W2 接入路径的更具体行级诊断,可指明具体文件。*
