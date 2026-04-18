# DS-160 Visa Simulator

FastAPI monolith for a DS-160-oriented nonimmigrant visa interview simulator.

## Quick Start

### Install

```bash
uv sync --dev
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

### Run API + Chainlit UI

`Chainlit` is mounted into the same FastAPI process under `/ui`.

```bash
uv run uvicorn app.main:app --reload
```

Then open:

- `http://127.0.0.1:8000/ui`

The UI is intentionally thin:

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
