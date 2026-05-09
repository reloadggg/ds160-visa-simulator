# DS-160 签证面签模拟器

这是一个面向 DS-160 / 美国非移民签证场景的智能问答与材料分析项目，当前形态为：

- 后端：`FastAPI`
- 前端：`Next.js`
- 数据存储：`SQLite`（默认）
- 模型接入：OpenAI Compatible 接口
- 运行模式：本地开发、前后端联调、Docker 一体化部署

项目目标不是做通用聊天应用，而是围绕签证问答、材料补充、会话记录、报告生成和面签风险提示，提供一个可迭代的 Agent 化工作流。

## 项目能力

当前仓库主要覆盖以下能力：

- 创建面签模拟会话
- 在会话中进行多轮问答
- 上传材料文件并触发抽取/解析
- 生成用户报告和内部分析报告
- 提供 OpenAI-compatible 对话入口
- 提供最小可用的后端进入鉴权，适合服务器测试阶段隔离访问
- 支持 Web 前端与备用 Chainlit UI

## 目录结构

仓库主要目录如下：

```text
app/                    FastAPI 后端主代码
  api/routers/          路由层
  services/             核心业务编排、抽取、打分、报告服务
  domain/               领域模型与跨层合同
  db/                   SQLAlchemy 模型与数据库初始化
  repositories/         数据访问层
  agents/               不同职责的 Agent 组件
  runtime_policies/     运行时模型配置
  policy_packs/         签证规则包
web/                    Next.js 前端
tests/                  单元 / 集成 / E2E / live 测试
fixtures/               测试夹具与样例数据
docker/                 Docker 启动脚本
docs/                   项目补充文档
.trellis/               项目开发规范、任务流与工作流辅助文件
```

## 技术栈

后端依赖见 `pyproject.toml`，核心包括：

- Python 3.12+
- FastAPI
- SQLAlchemy
- Pydantic v2
- Chainlit
- `pydantic-ai-slim[openai]`

前端位于 `web/`，核心包括：

- Next.js 16
- React 19
- TypeScript
- Tailwind CSS
- Radix UI

## 环境要求

建议本地准备以下环境：

- Python 3.12+
- `uv`
- Node.js 22+
- `pnpm`
- Docker / Docker Compose（如需容器部署）

## 快速开始

### 1. 安装后端依赖

```bash
uv sync --dev
```

### 2. 配置后端环境变量

从模板复制：

```bash
cp .env.example .env
```

运行时配置以 `.env` 为主，`app/runtime_policies/default.yaml` 只作为兜底默认值。

常用配置项：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `MULTIMODAL_EXTRACTION_ENABLED`
- `DATABASE_URL`
- `CORS_ALLOW_ORIGINS`
- `APP_AUTH_PASSWORD`
- `APP_AUTH_TOKEN_TTL_SECONDS`
- `RUNTIME_DEFAULT_PROVIDER`
- `RUNTIME_DEFAULT_MODEL`
- `RUNTIME_<MODULE>_<STAGE>_PROVIDER`
- `RUNTIME_<MODULE>_<STAGE>_REASONING_EFFORT`

示例：

```bash
RUNTIME_DEFAULT_MODEL=gpt-5.4
RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_REASONING_EFFORT=high
RUNTIME_SCORING_AGENT_INTERVIEW_TURN_REASONING_EFFORT=xhigh
```

### 3. 仅启动后端 API

```bash
uv run uvicorn app.main:app --reload
```

默认地址：

- API：`http://127.0.0.1:8000`
- 健康检查：`http://127.0.0.1:8000/healthz`

常用接口包括：

- `POST /v1/auth/login`
- `POST /v1/sessions`
- `GET /v1/sessions/{session_id}/required-package`
- `POST /v1/sessions/{session_id}/messages`
- `POST /v1/sessions/{session_id}/files`
- `GET /v1/sessions/{session_id}/reports/user`
- `GET /v1/sessions/{session_id}/reports/internal`
- `POST /v1/chat/completions`

## 前后端联调

新的 v0 前端位于 `web/`，推荐作为默认交互入口。

### 一键启动

```bash
make dev
```

辅助命令：

```bash
make status
make logs
make stop
```

`make dev` 会自动：

- 启动 FastAPI：`http://127.0.0.1:8000`
- 启动 Next.js：`http://127.0.0.1:3000`
- 首次缺少 `web/.env.local` 时自动从 `web/.env.example` 复制
- 首次缺少依赖时自动初始化 `.venv` 或 `web/node_modules`
- 把日志写入 `.dev/logs/`

如果端口冲突，可以自定义：

```bash
API_PORT=8001 WEB_PORT=3001 make dev
```

### 手动启动

先启动后端：

```bash
uv run uvicorn app.main:app --reload
```

再启动前端：

```bash
cd web
cp .env.example .env.local
pnpm install
pnpm dev
```

默认前端联调配置：

- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`
- `NEXT_PUBLIC_MOCK=false`

如果只需要前端静态演示，可将 `NEXT_PUBLIC_MOCK` 改为 `true`。

提示：

- 上传文件后若希望自动解析，请确认根目录 `.env` 中设置了 `PARSE_WORKER_INLINE=1`
- 只要根目录 `.env` 中配置了 `OPENAI_BASE_URL` 与 `OPENAI_API_KEY`，图片/PDF 材料会默认走多模态识别
- 只有显式设置 `MULTIMODAL_EXTRACTION_ENABLED=false` 才会关闭多模态材料抽取
- 修改根目录 `.env` 后，需要重启后端进程

## 简单进入鉴权

为了在“部署到服务器测试，但前端还没有完整用户系统”的阶段避免接口裸露，项目提供了最小可用的进入鉴权。

相关环境变量：

- `APP_AUTH_PASSWORD`
- `APP_AUTH_TOKEN_TTL_SECONDS`

行为约定：

- 当 `APP_AUTH_PASSWORD` 为空时，关闭进入鉴权，不影响本地开发
- 当 `APP_AUTH_PASSWORD` 有值时，前端或调用方需要先调用 `POST /v1/auth/login`
- 登录成功后获取 Bearer token
- 后续访问受保护业务接口时，需要带上 `Authorization: Bearer <token>`
- 健康检查等必要公开接口保持可访问

这个方案是“服务器测试隔离”用途，不是完整用户系统，不包含：

- 注册登录体系
- 用户表
- 权限分级
- 多租户隔离

## Docker 部署

项目提供一体化镜像：容器内部同时运行 FastAPI 后端和 Next.js 前端，默认只暴露前端端口 `3000`，前端通过 `/api` 反向代理访问容器内后端。

### 构建镜像

```bash
docker build -t ds160-agent2:latest .
```

### 运行示例

```bash
docker run -d --name ds160-agent2 \
  -p 3000:3000 \
  -v ds160-agent2-data:/data \
  -e APP_AUTH_PASSWORD='change-me' \
  -e OPENAI_BASE_URL='https://your-openai-compatible-endpoint/v1' \
  -e OPENAI_API_KEY='your-api-key' \
  -e CORS_ALLOW_ORIGINS='http://localhost:3000' \
  ds160-agent2:latest
```

常用命令：

```bash
docker logs -f ds160-agent2
docker stop ds160-agent2
docker rm ds160-agent2
```

### 使用 Compose

```bash
APP_AUTH_PASSWORD='change-me' \
OPENAI_BASE_URL='https://your-openai-compatible-endpoint/v1' \
OPENAI_API_KEY='your-api-key' \
docker compose up -d --build
```

Docker 相关约定：

- `APP_AUTH_PASSWORD` 为空时关闭进入鉴权；服务器测试环境建议设置强密码
- SQLite 默认写入 `/data/app.sqlite3`，建议通过 volume 持久化 `/data`
- 镜像内前端构建时使用 `NEXT_PUBLIC_API_BASE_URL=/api`
- 浏览器通常只需访问 `http://服务器:3000`
- 若部署到正式域名，可将 `CORS_ALLOW_ORIGINS` 设置为对应前端域名

## Chainlit 备用入口

项目保留了一个备用 Chainlit UI，挂载在同一个 FastAPI 进程下的 `/ui`。

启动后端：

```bash
uv run uvicorn app.main:app --reload
```

打开：

- `http://127.0.0.1:8000/ui`

说明：

- Chainlit 主要作为备用入口与回退验证通道
- 它不会与 `web/` 共享前端会话状态
- 它本身尽量保持轻量，只复用现有 API

## 测试与验证

### 默认测试

```bash
uv run pytest -q
```

### 跳过真实模型测试

```bash
uv run pytest -q -m "not live_llm"
```

### 典型回归验证

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_sessions_api.py \
  tests/integration/test_openai_compat.py \
  tests/integration/test_messages_api.py \
  tests/integration/test_interview_runtime_trace.py \
  tests/integration/test_reports_api.py
```

### Live LLM 测试

真实模型联调测试是可选项，需要显式提供模型配置：

```bash
RUN_LIVE_LLM_TESTS=1 \
OPENAI_BASE_URL=... \
OPENAI_API_KEY=... \
uv run pytest tests/integration/live -q -m live_llm
```

## 发布到 GitHub 前的建议

建议在推送前确认以下几点：

- `.env`、数据库文件、构建产物未被提交
- `web/.next`、`web/node_modules`、`.venv` 等本地产物已忽略
- 如果只想公开业务代码，不想公开本地 AI 开发辅助资产，需要额外检查 `.claude/`、`.gemini/`、`.trellis/` 等目录是否符合你的预期
- 首次发布建议先创建 `private` 仓库，确认内容后再决定是否公开

## 说明

当前仓库仍处于快速迭代阶段，README 以“帮助团队内部部署、联调和测试”为主要目标编写。后续如果要对外公开，建议再拆分出：

- 产品介绍版 README
- 架构设计文档
- API 使用文档
- 部署手册
