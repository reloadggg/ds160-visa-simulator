# DS-160 Visa Simulator

FastAPI monolith for a DS-160-oriented nonimmigrant visa interview simulator.

## Quick Start

### Install

```bash
uv sync --dev
```

### Runtime Config

日常运行时，runtime 相关配置以 `.env` 为主，`app/runtime_policies/default.yaml` 只作为兜底默认值。

推荐从 `.env.example` 复制一份：

```bash
cp .env.example .env
```

常用可调项包括：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `RUNTIME_DEFAULT_PROVIDER`
- `RUNTIME_DEFAULT_MODEL`
- `RUNTIME_<MODULE>_<STAGE>_PROVIDER`
- `RUNTIME_<MODULE>_<STAGE>_REASONING_EFFORT`

例如：

```bash
RUNTIME_DEFAULT_MODEL=gpt-5.4
RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_REASONING_EFFORT=high
RUNTIME_SCORING_AGENT_INTERVIEW_TURN_REASONING_EFFORT=xhigh
```

### Run API Only

```bash
uv run uvicorn app.main:app --reload
```

Available endpoints include:

- `POST /v1/sessions`
- `GET /v1/sessions/{session_id}/required-package`
- `POST /v1/sessions/{session_id}/messages`
- `POST /v1/sessions/{session_id}/files`
- `GET /v1/sessions/{session_id}/reports/user`
- `GET /v1/sessions/{session_id}/reports/internal`
- `POST /v1/chat/completions`

### Run API + Web Frontend

新的 v0 前端位于 `web/`，默认作为推荐联调入口。

最省事的方式：

```bash
make dev
```

常用辅助命令：

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
- 把日志写到 `.dev/logs/`

如果 `3000` 或 `8000` 已被占用，可以这样改端口：

```bash
API_PORT=8001 WEB_PORT=3001 make dev
```

如需手动分别启动，也可以继续用下面的命令。

先启动 FastAPI：

```bash
uv run uvicorn app.main:app --reload
```

然后启动前端：

```bash
cd web
cp .env.example .env.local
pnpm install
pnpm dev
```

默认本地联调配置：

- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`
- `NEXT_PUBLIC_MOCK=false`

如需只看前端演示，可以把 `NEXT_PUBLIC_MOCK` 改成 `true`。

提示：

- 上传文件后想自动解析，请确保根目录 `.env` 里有 `PARSE_WORKER_INLINE=1`
- 修改根目录 `.env` 后，需要重启后端进程

### Run API + Chainlit UI

`Chainlit` is mounted into the same FastAPI process under `/ui`.

```bash
uv run uvicorn app.main:app --reload
```

Then open:

- `http://127.0.0.1:8000/ui`

Chainlit 现在作为备用入口保留，便于回退和对照验证。它不会与 `web/` 共享前端会话状态。

The Chainlit UI is intentionally thin:

- It creates sessions through the existing API
- It forwards user messages to the existing message API
- It only prompts for file uploads when the backend requests documents
- It can show both user and internal reports

## Verification

### Core Phase 3 Regression

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_chainlit_mount.py \
  tests/unit/test_chainlit_client.py \
  tests/integration/test_sessions_api.py \
  tests/integration/test_openai_compat.py \
  tests/integration/test_messages_api.py \
  tests/integration/test_parse_worker.py \
  tests/integration/test_gate_review_runtime.py \
  tests/integration/test_interview_runtime_trace.py \
  tests/integration/test_reports_api.py \
  tests/unit/test_gate_runtime_service.py \
  tests/unit/test_gate_service.py \
  tests/unit/test_runtime_models.py \
  tests/unit/test_session_schema_bootstrap.py \
  tests/unit/test_profile_recompute_service.py \
  tests/unit/test_interview_runtime_service.py \
  tests/unit/test_report_service.py
```

### Live LLM Tests

These remain optional and require explicit model credentials.

```bash
RUN_LIVE_LLM_TESTS=1 \
OPENAI_BASE_URL=... \
OPENAI_API_KEY=... \
uv run pytest tests/integration/live -q -m live_llm
```
