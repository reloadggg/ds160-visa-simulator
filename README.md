# DS-160 AI 面签模拟器

> 面向美国非移民签证准备的 AI 面签工作台：用多轮追问、材料理解、Case Board、证据图谱和复盘报告，帮助申请人在正式 DS-160 / 面签前发现叙事缺口、材料冲突和高风险信号。

![Architecture](docs/assets/architecture.svg)

## 授权协议：禁止商用

本仓库采用“源码可见、非商业使用”的自定义许可，完整条款见 [LICENSE](LICENSE)。这不是 MIT、Apache、GPL 等开放商用开源协议。

允许：个人学习、研究、教学、内部评估和非商业原型验证；在非商业场景下复制、修改、运行和分发，但必须保留 [LICENSE](LICENSE) 与版权声明。

禁止：未经书面授权用于付费产品、SaaS、咨询交付、商业内部系统、客户项目、商业模型训练、商业数据产品或任何直接/间接营利业务。

如需商业使用、商业部署或商业集成，必须先取得版权所有者的书面商业授权。

## 这个项目是什么

DS-160 AI 面签模拟器不是普通聊天机器人，也不是“材料齐了才允许聊”的表单系统。它更像一个签证准备工作台：用户选择签证类别后，系统围绕赴美目的、资金来源、学习/工作计划、回国约束、材料证据和 DS-160 叙事一致性持续追问，并把每轮对话和每份材料沉淀为可复盘的 Case Memory / Evidence Graph。

适合用于：

- 模拟签证官式追问，而不是泛泛闲聊；
- 上传 I-20、offer、资金证明、关系证明、护照页等材料，并由多模态模型理解可见事实；
- 通过 Case Board 展示已知事实、证据片段、冲突、待核实点和下一问原因；
- 接入服务端 RAG，优先引用官方政策、领馆页面和互惠表资料；
- 生成用户准备报告、内部调试报告、会话导出和复盘报告；
- 验证 native interviewer runtime、材料理解、Case Memory、RAG、Governor 护栏和前端工作台体验。

## 主要用户流程

```text
选择签证类别
  ↓
面签式对话：回答问题、补充背景、澄清风险点
  ↓
上传材料：PDF / 图片 / 文本材料进入材料理解队列
  ↓
Case Board 更新：事实、证据、冲突、证明点、下一步建议
  ↓
继续追问或复盘：报告、调试台、导出、后台管理
```

工作台中的典型区域：

- 左侧：会话历史、材料库、设置、报告和后台入口；
- 中间：面签问答流，失败消息可保留原文并重试；
- 右侧：Case Board，展示案例事实、证据、冲突和下一问依据；
- 后台：access key 发放、会话查看、运行时模型配置、RAG 状态和调试开关。

## 架构一览

```text
Next.js Workbench
        │  /api/v1 in browser / proxy
        ▼
FastAPI API Layer
        │  /v1 direct backend
        ▼
Message / File / Report Services
        │
        ├── MaterialUnderstandingService
        ├── CaseMemoryService / Evidence Graph
        ├── RuntimeDebugSnapshotService
        └── Report / Review / Export
        │
        ▼
NativeInterviewerRuntimeService
        │
        ├── Case State Projection
        ├── Typed LLM Runtime
        ├── Visa Policy RAG / Chroma / SiliconFlow
        ├── Governor / Grounding Guard
        └── Legacy Gate Compatibility Projection
```

| 层级 | 作用 |
| --- | --- |
| `web/` | Next.js 16 前端工作台，负责会话、材料、报告、历史、设置和鉴权体验 |
| `app/api/routers/` | FastAPI 路由层，对外暴露 auth、session、message、file、report、admin、RAG、OpenAI-compatible API |
| `app/services/` | 业务编排层，承载 native interviewer runtime、材料理解、Case Memory、报告、运行时快照和状态同步 |
| `app/agents/` | Agent 运行单元，负责问题生成、材料复核、裁决和结构化输出 |
| `app/domain/` | 领域模型与跨层合同，例如 Case Memory、证据卡、运行状态和决策结构 |
| `app/integrations/` | 外部模型、embedding、rerank、文件解析等集成适配 |
| `app/repositories/` | 会话、材料、turn record、access key 等持久化访问 |
| `app/policy_packs/` | F-1、J-1、B-1/B-2、H-1B 等签证类别规则包 |

## 核心概念

| 概念 | 简明说明 |
| --- | --- |
| Native interviewer runtime | 当前公开主流程。每轮只产生一条用户可见面试官回复，并通过结构化字段给前端、报告和调试台消费。 |
| Case Memory / Evidence Graph | 长期事实、材料证据、冲突和证明点的持久化来源；LLM 上下文不是最终状态源。 |
| Case Board | 前端主视图，展示 claims、evidence cards、proof points、conflicts 和 next move。 |
| 材料理解 | 上传文件先保存并进入 `case_understanding` 队列；图片/PDF 由多模态模型理解，文件名只作为审计元数据。 |
| RAG | 服务端政策知识库能力；使用 Chroma + SiliconFlow embedding/rerank，不由用户 BYOK 配置覆盖。 |
| Admin console | 后台可以登录、发放 access keys、查看 key 关联会话、调整 demo 设置和测试运行时模型。 |
| Access keys | 管理员发放给用户的访问密钥，并把用户历史隔离到 `history_namespace=key_<id>`。登录本身不消耗 quota；创建新 session 时才扣次数，禁用/过期/耗尽后仍可回到已绑定历史，但不能创建新会话。 |
| Runtime model config | 后台可保存 OpenAI-compatible `Base URL`、`API Key`、`Model` 和 streaming 设置；测试接口会返回配置来源 `draft` / `admin` / `env`，不会回显 secret。 |
| 失败消息重试 | 前端保留失败用户消息的 `client_message_id` 和 `retry_content`；重试同一条消息时复用 idempotency key，已完成请求返回 `idempotent_replay`，处理中请求返回 `409`。 |
| Runtime debug snapshot | 调试台读取 `GET /v1/sessions/{session_id}/debug/runtime`，返回一次只读快照；敏感字段会被后端 redaction，不应当作写入接口或实时订阅。 |

更深的运行时合同见 [Runtime Contracts](docs/runtime-contracts.md)、[Agent Runtime Spec](docs/architecture/agent-runtime-spec.md) 和 [AI-native Case Understanding Spec](docs/architecture/ai-native-case-understanding-spec.md)。

## 快速启动

### 1. 准备后端环境

```bash
uv sync --dev
cp .env.example .env
```

至少配置一个 OpenAI-compatible 模型服务：

```env
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_API_KEY=your-api-key
```

常用可选项：

```env
APP_AUTH_PASSWORD=
APP_AUTH_SESSION_TTL_SECONDS=86400
APP_AUTH_IDLE_TIMEOUT_SECONDS=28800
APP_AUTH_COOKIE_SECURE=true
APP_AUTH_COOKIE_SAMESITE=lax
APP_AUTH_PROTECT_DOCS=true
APP_COMPAT_API_KEY=
CORS_ALLOW_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
ALLOW_USER_MODEL_CONFIG=false
ALLOW_USER_MODEL_STREAMING=false
ALLOW_RUNTIME_DEBUG=false
ALLOW_DEBUG_FILL=false
```

本地单进程开发不设置 `DATABASE_URL` 时默认使用 `sqlite:///./app.sqlite3`。Docker Compose 默认使用内置 Postgres；如需外部数据库，设置 `COMPOSE_DATABASE_URL`。

### 2. 启动前后端

推荐使用一键开发命令：

```bash
make dev
```

默认地址：

- 前端工作台：`http://127.0.0.1:3000`
- 后端 API：`http://127.0.0.1:8000`
- 健康检查：`http://127.0.0.1:8000/healthz`

如果端口冲突：

```bash
API_PORT=8001 WEB_PORT=3001 make dev
```

### 3. 可选：开启 RAG

```env
RAG_ENABLED=true
RAG_CHROMA_PATH=./data/chroma/us_visa
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_API_KEY=your-siliconflow-api-key
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
SILICONFLOW_RERANK_MODEL=Qwen/Qwen3-Reranker-4B
```

RAG 与用户自带模型配置职责分离：`RAG_*` / `SILICONFLOW_*` 只由服务端读取；用户设置页只影响对话模型，不影响 embedding/rerank。

## Docker / 部署

项目提供同一个 Docker 镜像，但推荐 Compose 拆分职责：`ds160-api`、`ds160-web`、`ds160-worker`、`postgres` 和 `nginx`。

```bash
docker build -t ds160-agent2:latest .
```

本地或极简单容器试跑：

```bash
docker run -d --name ds160-agent2 \
  -p 3000:3000 \
  -v ds160-agent2-data:/data \
  -e APP_AUTH_PASSWORD='change-me' \
  -e OPENAI_BASE_URL='https://your-openai-compatible-endpoint/v1' \
  -e OPENAI_API_KEY='your-api-key' \
  ds160-agent2:latest
```

服务器/生产风格部署优先使用 Compose：

```bash
APP_AUTH_PASSWORD='change-me' \
OPENAI_BASE_URL='https://your-openai-compatible-endpoint/v1' \
OPENAI_API_KEY='your-api-key' \
NEXT_PUBLIC_GIT_SHA="$(git rev-parse --short HEAD)" \
APP_GIT_SHA="$(git rev-parse --short HEAD)" \
NEXT_PUBLIC_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
docker compose up -d --build postgres ds160-api ds160-web ds160-worker
```

`nginx` 是 TLS 入口，`/api` 指向 API，其余路径指向 Web。完整部署说明见 [deploy/README.md](deploy/README.md)。已有 SQLite 数据切到 Postgres 前，先按 [Postgres Migration Runbook](docs/architecture/postgres-migration-runbook.md) 做备份和 smoke test。

## 访问保护与后台

- `APP_AUTH_PASSWORD` 为空时关闭普通用户鉴权，方便本地开发。
- 设置 `APP_AUTH_PASSWORD` 后，浏览器先调用 `POST /v1/auth/login`，后端通过 `HttpOnly` Cookie 保护业务接口。
- `ADMIN_AUTH_PASSWORD` 可单独设置后台密码；未设置时后台使用 `APP_AUTH_PASSWORD` 作为 fallback。
- 后台登录后可发放 access keys，用户用 access key 调用普通登录接口即可进入工作台。
- 生产环境默认保护 `/docs`、`/redoc`、`/openapi.json`，可通过 `APP_AUTH_PROTECT_DOCS=false` 调整。
- 外部机器客户端调用 `/v1/chat/completions` 或 `/v1/responses` 时，应配置 `APP_COMPAT_API_KEY` 并使用 `Authorization: Bearer <token>`。

这只是测试阶段的进入保护，不是完整多租户用户系统。

## 主要 API

完整认证方式、base URL、SSE 事件、错误合同和示例见 [API Guide](docs/API.md)。

| 分组 | 代表接口 |
| --- | --- |
| App / version / health | `GET /v1/app-config`、`GET /version`、`GET /healthz` |
| Auth | `POST /v1/auth/login`、`GET /v1/auth/me`、`POST /v1/auth/logout` |
| Sessions | `POST /v1/sessions`、`GET /v1/sessions`、`GET /v1/sessions/{session_id}/required-package` |
| Messages | `GET /v1/sessions/{session_id}/messages`、`POST /v1/sessions/{session_id}/messages`、`POST /v1/sessions/{session_id}/messages/stream` |
| Files / materials | `POST /v1/sessions/{session_id}/files`、`GET /v1/material-packages`、`POST /v1/sessions/{session_id}/material-packages/{package_id}/import` |
| Reports | `GET /v1/sessions/{session_id}/reports/user`、`POST /v1/sessions/{session_id}/reports/review`、`GET /v1/sessions/{session_id}/reports/export` |
| RAG | `GET /v1/rag/status`、`POST /v1/rag/files`、`GET /v1/admin/rag/status` |
| Admin | `POST /v1/admin/login`、`GET /v1/admin/access-keys`、`PATCH /v1/admin/settings`、`POST /v1/admin/model-config/test` |
| OpenAI-compatible | `POST /v1/chat/completions`、`POST /v1/responses` |
| Debug | `GET /v1/sessions/{session_id}/debug/runtime`、`POST /v1/sessions/{session_id}/debug/material-bundles/stream` |

## 常用验证命令

```bash
# 后端集成测试
uv run pytest tests/integration

# 前端类型检查 / lint / test
cd web
pnpm install
pnpm lint
pnpm test

# 查看路由和文档路径引用
rg -n "include_router|@router\." app/main.py app/api/routers
rg -n "/v1/(auth|sessions|admin|rag|model-config|chat|responses)" docs/API.md app/api/routers web/lib/api/client.ts
```

可选 live LLM 测试需要显式配置模型服务和 `RUN_LIVE_LLM_TESTS=true`。

## 本地数据与版本控制

- `app.sqlite3`、`data/`、上传文件、Chroma 索引和本地 `.env` 都不应提交。
- Compose 默认使用 Postgres volume；单容器/本地开发默认 SQLite。
- 前端版本来自 `web/package.json`，也可用 `NEXT_PUBLIC_APP_VERSION`、`NEXT_PUBLIC_GIT_SHA`、`NEXT_PUBLIC_BUILD_TIME` 覆盖。
- 后端版本来自 `app/core/app_version.py`，也可用 `APP_GIT_SHA`、`APP_BUILD_TIME` 覆盖。
- 部署前建议递增或覆盖版本信息，避免 UI 无法判断是否已更新。

## 继续阅读

- [API Guide](docs/API.md)
- [Runtime Contracts](docs/runtime-contracts.md)
- [Agent Runtime Spec](docs/architecture/agent-runtime-spec.md)
- [AI-native Case Understanding Spec](docs/architecture/ai-native-case-understanding-spec.md)
- [RAG Knowledge Spec](docs/architecture/rag-knowledge-spec.md)
- [Deployment Guide](deploy/README.md)
