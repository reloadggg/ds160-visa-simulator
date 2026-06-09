# DS-160 AI 面签模拟器

> 一个面向美国非移民签证准备的 AI 面签工作台。它把材料理解、签证官式追问、Case Board 和复盘报告放在同一条链路里，帮助申请人在正式填写 DS-160 或进入面签前，先发现自己叙事里的空白、矛盾和高风险点。

![Architecture](docs/assets/architecture.svg)

## 这个项目是什么

很多签证准备工具会把问题拆成清单：有没有 I-20、有没有资金证明、有没有行程、有没有工作证明。清单很有用，但真正让申请人焦虑的通常不是“有没有文件”，而是：

- 我说的故事和材料能不能互相支撑？
- 面签官追问资金、学习计划、回国约束或家庭关系时，我会不会前后不一致？
- 哪些信息已经有证据，哪些只是口头说法，哪些地方还需要补材料或澄清？

DS-160 AI 面签模拟器就是围绕这些问题做的。它不是普通聊天机器人，也不是材料上传表单；它更像一个可复盘的签证准备桌面：用户先选择签证类别，像真实面签一样回答问题；系统在对话过程中理解上传材料，把材料事实、用户陈述、冲突点和待证明事项沉淀到 Case Memory / Evidence Graph；前端再用 Case Board 把这些状态展示出来，让用户知道“为什么现在问这个问题”和“下一步应该补什么”。

当前公开用户流程由 `native_interviewer` 负责写入用户可见回复和材料刷新结果。历史 runtime、graph / shadow / eval 相关内容只作为兼容、回放、评估或架构演进语境存在，不是普通用户可以切换的公开面谈模式。

## 一次典型使用

用户视角下，它的使用方式很接近一次有记录、有证据板的模拟面谈。公开根路径 `/` 现在先展示产品价值与系统入口；用户在首页打开授权弹窗并验证 access key 后，会进入 `/login` 下的完整模拟面签工作台。后台可以为某个 access key 生成一键分享链接；已用 Key 登录的用户也可以在工作台设置里复制本 Key 的分享链接。用户打开 `/#ds160_access_key=...` 后只需点击启用即可进入工作台，不需要再次手输 Key。微信 web-view 轻量入口是 `/wx`，用于小程序壳内打开同一套授权和材料上传体验。运维或演示前也可以从首页进入 `/health` 查看项目状态页。

1. 选择签证类别，例如 F-1、J-1、B-1/B-2 或 H-1B。
2. 进入面签式对话，回答赴美目的、资金来源、学习/工作计划、家庭约束和回国安排等问题。
3. 在系统追问或自己需要补充时上传材料，例如 I-20、offer、资金证明、护照页、关系证明、行程或在职证明；桌面端走 Web 上传，小程序壳可以从 `/wx` 生成短期 upload ticket 后跳到原生 `wx.uploadFile` 上传页。
4. 等待材料理解完成后，Case Board 会更新已知事实、证据片段、冲突、证明点和下一步建议。
5. 继续回答追问，直到关键风险被澄清，再生成用户准备报告、内部复盘报告或导出会话。

工作台里通常会看到四块内容：左侧是会话历史、材料库、设置、报告和后台入口；中间是面签问答流，失败消息可以保留原文并重试；右侧是 Case Board，集中展示事实、证据、冲突和下一问依据；后台用于 access key 发放、会话查看、运行时模型配置、RAG 状态和受控调试。用户显示名不再在登录时固定，而是在工作台设置里保存或修改。首页和状态页里的 GitHub 链接由后台公开配置 `show_github_link` 控制，默认隐藏。

## 为什么材料理解、追问和 Case Board 要放在一起

单独做材料解析，只能告诉用户“文件里写了什么”；单独做聊天，只容易变成泛泛建议；单独做报告，又经常缺少过程证据。这个项目把三者连起来，是为了让准备过程更接近真实面签压力：

- **材料理解**负责把 PDF、图片或文本材料里的可见事实抽出来，文件名只作为审计信息，不能替代内容理解。
- **面签追问**负责根据当前签证类别、用户回答、材料证据和风险状态继续发问，而不是固定问卷走完就结束。
- **Case Board**负责把系统“已经知道什么、证据来自哪里、哪里冲突、下一步为什么这样问”摊开给用户和维护者看，避免 LLM 上下文变成不可检查的黑盒。

因此，项目的价值不只是“AI 帮你练习回答”，而是让一次模拟面谈留下结构化状态：后续报告、复盘、材料补充、RAG 引用和调试快照都可以围绕同一份 case state 展开。

## 适合谁使用

- 需要准备美国非移民签证面签、想提前暴露叙事风险的申请人或演示用户。
- 想验证 F-1 等签证场景材料包、面谈追问和报告链路能否跑通的维护者。
- 想研究 AI-native case understanding、材料证据图谱、签证政策 RAG 和结构化 runtime 合同的开发者。
- 想在受控环境里演示“材料 + 对话 + 复盘”闭环的团队。

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
        └── Gate Compatibility Projection (historical fields only)
```

| 层级 | 作用 |
| --- | --- |
| `web/` | Next.js 16 前端，包含公开产品首页、内嵌授权弹窗、`/login` 工作台、`/wx` 微信 web-view 工作台、`/health` 状态页、后台入口、会话、材料、报告、历史、设置和鉴权体验 |
| `miniprogram/` | 微信小程序源码壳，当前提供 web-view 入口和原生聊天文件上传页，不实现完整原生重写 |
| `app/api/routers/` | FastAPI 路由层，对外暴露 auth、session、message、file、report、admin、RAG、OpenAI-compatible API 和微信 upload ticket |
| `app/services/` | 业务编排层，承载 native interviewer runtime、材料理解、Case Memory、报告、运行时快照、状态同步和微信上传凭证 |
| `app/agents/` | Agent 运行单元，负责问题生成、材料复核、裁决和结构化输出 |
| `app/domain/` | 领域模型与跨层合同，例如 Case Memory、证据卡、运行状态和决策结构 |
| `app/integrations/` | 外部模型、embedding、rerank、文件解析等集成适配 |
| `app/repositories/` | 会话、材料、turn record、access key 等持久化访问 |
| `app/policy_packs/` | F-1、J-1、B-1/B-2、H-1B 等签证类别规则包 |

## 核心概念

| 概念 | 简明说明 |
| --- | --- |
| Native interviewer runtime | 当前唯一公开面谈主流程。每轮只产生一条用户可见面试官回复，并通过结构化字段给前端、报告和调试台消费。 |
| Case Memory / Evidence Graph | 长期事实、材料证据、冲突和证明点的持久化来源；LLM 上下文不是最终状态源。 |
| Case Board | 前端主视图，展示 claims、evidence cards、proof points、conflicts 和 next move，让用户能看见系统为什么继续追问。 |
| 材料理解 | 上传文件先保存并进入 `case_understanding` 队列；图片/PDF 由多模态模型理解，文件名只作为审计元数据。 |
| RAG | 服务端政策知识库能力；使用 Chroma + SiliconFlow embedding/rerank，不由用户 BYOK 配置覆盖。 |
| Admin console | 后台可以登录、发放 access keys、查看 key 关联会话、调整 demo 设置和测试运行时模型。 |
| Access keys | 管理员发放给用户的访问密钥，并把用户历史隔离到 `history_namespace=key_<id>`。登录本身不消耗 quota；创建新 session 时才扣次数，禁用/过期/耗尽后仍可回到已绑定历史，但不能创建新会话。后台支持直接 `复制 Key` 和生成 `/#ds160_access_key=...` 一键分享链接；分享链接等同于持有该 Key，应配合用量、过期时间和转发范围管理。 |
| 微信 web-view / upload ticket | `/wx` 是小程序 web-view 轻量 H5 入口；原生上传页通过短期 upload ticket 上传微信聊天文件，ticket 默认 300 秒有效、最多 5 个文件，后端只保存 ticket hash。 |
| Runtime model config | 后台可保存 OpenAI-compatible `Base URL`、`API Key`、`Model` 和 streaming 设置；测试接口会返回配置来源 `draft` / `admin` / `env`，不会回显 secret。 |
| 失败消息重试 | 前端保留失败用户消息的 `client_message_id` 和 `retry_content`；重试同一条消息时复用 idempotency key，已完成请求返回 `idempotent_replay`，处理中请求返回 `409`。 |
| Runtime debug snapshot | 调试台读取 `GET /v1/sessions/{session_id}/debug/runtime`，返回一次只读快照；敏感字段会被后端 redaction，不应当作写入接口或实时订阅。 |

更深的运行时合同见 [Runtime Contracts](docs/runtime-contracts.md)、[Agent Runtime Spec](docs/architecture/agent-runtime-spec.md) 和 [AI-native Case Understanding Spec](docs/architecture/ai-native-case-understanding-spec.md)。

## 授权协议：禁止商用

本仓库采用“源码可见、非商业使用”的自定义许可，完整条款见 [LICENSE](LICENSE)。这不是 MIT、Apache、GPL 等开放商用开源协议。

允许：个人学习、研究、教学、内部评估和非商业原型验证；在非商业场景下复制、修改、运行和分发，但必须保留 [LICENSE](LICENSE) 与版权声明。

禁止：未经书面授权用于付费产品、SaaS、咨询交付、商业内部系统、客户项目、商业模型训练、商业数据产品或任何直接/间接营利业务。

如需商业使用、商业部署或商业集成，必须先取得版权所有者的书面商业授权。

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

- 产品首页：`http://127.0.0.1:3000`
- 用户工作台：`http://127.0.0.1:3000/login`
- 微信 web-view 工作台：`http://127.0.0.1:3000/wx`
- 项目状态页：`http://127.0.0.1:3000/health`
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

- 前端 `/` 是公开产品首页；用户 CTA 会在首页打开授权弹窗，授权成功后进入 `/login` 工作台。
- `/login` 保留原有普通用户 `AuthGuard` / 工作台流程，也可以作为直接访问工作台的备用入口。
- `/wx` 是微信小程序 web-view 轻量入口，支持普通 access key 和后台生成的分享 key；它不依赖 `wx.login` 或 OpenID 绑定。
- `/health` 是前端状态页，会读取后端 `/healthz` 并用同一套科技风视觉展示 app、database、LLM、worker 等检查项。
- `/admin` 是后台入口。
- `APP_AUTH_PASSWORD` 为空时关闭普通用户鉴权，方便本地开发。
- 设置 `APP_AUTH_PASSWORD` 后，浏览器先调用 `POST /v1/auth/login`，后端通过 `HttpOnly` Cookie 保护业务接口。
- `ADMIN_AUTH_PASSWORD` 可单独设置后台密码；未设置时后台使用 `APP_AUTH_PASSWORD` 作为 fallback。
- 后台登录后可发放 access keys，用户用 access key 调用普通登录接口即可进入工作台；后台 access key 卡片提供 `显示明文`、`复制 Key` 和 `一键分享链接`，复制按钮会直接复制当前 Key，不再要求先点选再到另一区域复制。用 Key 登录后的普通用户也可以在工作台设置里复制本 Key 分享链接；前端只保留本次 Key 登录的明文到 sessionStorage，退出/过期/Key 不匹配会清理。
- 一键分享链接使用 hash 参数 `/#ds160_access_key=<access-key-secret>`，用户打开后在首页、`/login` 或 `/wx` 点击启用即可授权；授权成功后前端会清理地址栏里的 Key。兼容 query 参数只用于旧链接，推荐始终使用 hash，避免 Key 进入服务端访问日志。
- 用户显示名属于工作台设置项，不是后端账号字段；换浏览器或清理本地存储后可能需要重新设置。
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
| WeChat upload ticket | `POST /v1/sessions/{session_id}/upload-ticket`、`GET /v1/wx/upload-tickets/{ticket}`、`POST /v1/wx/upload-tickets/{ticket}/files` |
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

- [文档导航](docs/README.md)
- [API Guide](docs/API.md)
- [Runtime Contracts](docs/runtime-contracts.md)
- [Agent Runtime Spec](docs/architecture/agent-runtime-spec.md)
- [AI-native Case Understanding Spec](docs/architecture/ai-native-case-understanding-spec.md)
- [RAG Knowledge Spec](docs/architecture/rag-knowledge-spec.md)
- [WeChat Mini Program lightweight entry](docs/wechat-miniprogram-mvp.md)
- [Deployment Guide](deploy/README.md)
