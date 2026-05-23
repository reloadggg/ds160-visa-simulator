# DS-160 API Reference

本文件记录当前 FastAPI 后端对外暴露的主要接口。代码入口位于 `app/api/routers/`，默认前缀为 `/v1`；前端在生产镜像中通过 `/api` 反向代理访问后端，因此浏览器侧实际 URL 通常是 `/api/v1/...`。

## Authentication

### Web Cookie Session

当 `APP_AUTH_PASSWORD` 为空时，鉴权关闭，方便本地开发和测试。

当 `APP_AUTH_PASSWORD` 非空时，除公开入口外的业务接口都需要先登录：

```http
POST /v1/auth/login
Content-Type: application/json

{"password":"<APP_AUTH_PASSWORD>"}
```

成功后后端会创建服务端会话，并设置 `HttpOnly` Cookie。前端不需要、也不能读取长期 token；后续请求由浏览器自动携带 Cookie。

安全相关默认行为：

- Cookie 名称默认是 `ds160_session`
- Cookie 使用 `HttpOnly`、`Secure`、`SameSite=lax`
- 非安全方法会执行 Origin/Referer 校验
- `/docs`、`/redoc`、`/openapi.json` 默认在开启鉴权时受保护
- `?access_token=` 不再作为通用认证方式

### Machine Access

`POST /v1/chat/completions` 可作为 OpenAI-compatible 机器接口使用。开启 `APP_AUTH_PASSWORD` 后，如果外部客户端不走浏览器 Cookie，需要单独配置：

```env
APP_COMPAT_API_KEY=<machine-token>
```

然后使用：

```http
Authorization: Bearer <machine-token>
```

不要把 Web 登录 Cookie 当作机器接口的长期凭据。

## Common Responses

常见错误格式：

```json
{"detail":"authentication required"}
```

常见状态码：

| Status | Meaning |
| --- | --- |
| `401` | 未登录、会话过期、会话已撤销 |
| `403` | CSRF 校验失败、功能开关未开启或权限不足 |
| `404` | 会话、文件或功能入口不存在 |
| `409` | 会话状态不允许当前操作 |
| `413` | 上传文件超过大小限制 |
| `415` | 上传文件类型不支持 |
| `422` | 请求字段、签证类型或上传内容不可解析 |
| `502` | 外部模型、RAG 索引或供应商调用失败 |

## Auth API

### `POST /v1/auth/login`

建立服务端登录会话。

Request:

```json
{
  "password": "test-password"
}
```

Response:

```json
{
  "authenticated": true,
  "expires_in": 86400
}
```

Notes:

- 登录失败会记录短窗口失败次数并触发限流。
- 默认限流配置为 `APP_AUTH_LOGIN_RATE_LIMIT_ATTEMPTS` 和 `APP_AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS`。

### `GET /v1/auth/me`

查询当前登录状态。

Response when authenticated:

```json
{
  "authenticated": true,
  "expires_at": "2026-05-23T12:34:56Z"
}
```

Response when unauthenticated:

```json
{
  "authenticated": false,
  "expires_at": null
}
```

### `POST /v1/auth/logout`

撤销当前服务端会话并清理 Cookie。

Response:

```json
{
  "authenticated": false,
  "expires_at": null
}
```

## Session API

### `POST /v1/sessions`

创建一个签证模拟会话。

Request:

```json
{
  "declared_family": "f1"
}
```

支持的后端签证类型 code：

- `f1`
- `j1`
- `b1_b2`
- `h1b`

Response:

```json
{
  "session_id": "sess-abc123",
  "phase_state": "intake",
  "current_governor_decision": "need_more_evidence",
  "gate_status": {}
}
```

### `GET /v1/sessions/{session_id}/required-package`

返回当前签证类型的初始材料包。

Response:

```json
{
  "required_initial_package": ["ds160", "passport_bio"],
  "required_initial_package_labels": ["DS-160 确认页", "护照信息页"]
}
```

### `POST /v1/sessions/{session_id}/debug/fill-current-gap`

本地调试入口，默认关闭。需要 `ALLOW_DEBUG_FILL=true`。

Request:

```json
{
  "scenario": "normal"
}
```

## Messages API

### `POST /v1/sessions/{session_id}/messages`

提交一轮用户回答，并返回面试官下一步回复。

Request:

```json
{
  "role": "user",
  "content": "I will study computer science in the US.",
  "model_config": {
    "base_url": "https://example.com/v1",
    "api_key": "user-key",
    "model": "gpt-compatible-model"
  }
}
```

`model_config` 是可选字段。只有 `ALLOW_USER_MODEL_CONFIG=true` 时，后端才接受用户自带模型配置；API Key 只在本次请求中使用，不写入数据库。

Response includes stable runtime fields consumed by the frontend:

```json
{
  "assistant_message": "string",
  "governor_decision": "continue_interview",
  "requested_documents": [],
  "remaining_required_documents": [],
  "gate_progress": {},
  "score_summary": {},
  "turn_decision": {},
  "document_review": {},
  "prompt_trace": {},
  "runtime_view_state": {}
}
```

### `POST /v1/sessions/{session_id}/messages/stream`

事件式 SSE 入口。需要 `ALLOW_USER_MODEL_STREAMING=true`。

事件类型：

- `accepted`
- `analyzing`
- `final`
- `error`

当前流式接口展示处理阶段和最终结果，不提供 token 级逐字流。

## Files API

### `POST /v1/sessions/{session_id}/files`

上传申请材料。接口使用 `multipart/form-data`。

Fields:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | PDF、PNG、JPG 或 JPEG 文件 |
| `document_type` | no | 用户显式纠偏的材料类型 |
| `context_text` | no | 同一条聊天消息里的原始用户文本 |

`context_text` 只由前端原样透传，材料类型判断在后端完成。

Response:

```json
{
  "document_id": "doc-abc123",
  "document_status": "uploaded",
  "job_id": "job-123",
  "job_status": "queued",
  "document_type": "funding_proof",
  "document_assessment": {},
  "document_type_candidates": ["funding_proof"],
  "relevance": "medium",
  "supported_claims": ["/funding/primary_source"],
  "confidence": 0.65,
  "feedback_message": "string",
  "relevant": true,
  "main_flow_feedback": {},
  "requested_documents": [],
  "remaining_required_documents": [],
  "gate_progress": {}
}
```

### `GET /v1/sessions/{session_id}/files/{document_id}/content`

返回已上传文件的原始内容，用于图片/PDF 预览。

Notes:

- 文件必须属于 URL 中的 `session_id`
- 开启鉴权时使用同一个 HttpOnly Cookie
- 不支持长期 query token

## Reports API

### `GET /v1/sessions/{session_id}/reports/user`

返回面向用户的准备建议报告。

### `GET /v1/sessions/{session_id}/reports/internal`

返回内部调试报告，包含 runtime ledger、trace、score history 和 governor history。

### `POST /v1/sessions/{session_id}/reports/review`

生成面试复盘报告。

### `GET /v1/sessions/{session_id}/reports/export`

导出当前会话快照，包含会话、用户报告、内部报告、profile snapshot 和材料摘要。

## Model Config API

### `POST /v1/model-config/models`

使用用户提供的 OpenAI-compatible `base_url` 和 `api_key` 代理请求 `/models`，用于前端模型下拉列表。

需要：

```env
ALLOW_USER_MODEL_CONFIG=true
```

Request:

```json
{
  "base_url": "https://example.com/v1",
  "api_key": "user-key"
}
```

Response:

```json
{
  "models": [
    {"id": "model-id", "label": "model-id"}
  ]
}
```

## RAG API

### `GET /v1/rag/status`

返回服务端知识库状态。该接口只读，不应创建空 Chroma collection。

Response shape:

```json
{
  "enabled": true,
  "ready": false,
  "status": "available",
  "skip_reason": null,
  "vector_store": "chroma",
  "index_version": "v1",
  "collection_prefix": "us_visa",
  "chroma_mode": "persistent",
  "embedding_model": "BAAI/bge-m3",
  "rerank_model": "Qwen/Qwen3-Reranker-4B",
  "upload_max_size_mb": 32,
  "allow_third_party_reference": false,
  "collections": []
}
```

### `POST /v1/rag/files`

上传第三方知识库资料并写入 RAG 索引。公共入口固定写入 `source_type=third_party_reference`，不能提升为官方来源。

Fields:

| Field | Required | Description |
| --- | --- | --- |
| `file` | yes | PDF、DOCX、文本等后端支持的知识库文件 |
| `title` | no | 来源标题 |
| `url` | no | 来源 URL |
| `visa_family` | no | 签证类型 |
| `country` | no | 国家 |
| `post` | no | 领馆/地区 |
| `section_path` | no | 章节路径 |

Response:

```json
{
  "status": "indexed",
  "source_id": "source-id",
  "source_type": "third_party_reference",
  "title": "source title",
  "collection_name": "us_visa_third_party_reference_v1",
  "chunk_count": 10,
  "skipped": false,
  "skip_reason": null
}
```

## OpenAI-Compatible API

### `POST /v1/chat/completions`

兼容 OpenAI Chat Completions 的对话入口。

Request:

```json
{
  "model": "ds160-runtime",
  "messages": [
    {"role": "user", "content": "I want to apply for an F-1 visa."}
  ],
  "metadata": {
    "declared_family": "f1",
    "session_id": "sess-existing"
  }
}
```

Rules:

- `messages` 至少包含一条 `user` 消息
- `metadata.session_id` 存在时复用会话
- `metadata.session_id` 不存在时需要 `metadata.declared_family`
- 开启 `APP_AUTH_PASSWORD` 后，外部机器调用应使用 `APP_COMPAT_API_KEY`

Response:

```json
{
  "id": "chatcmpl-sess-abc123",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "string"},
      "finish_reason": "stop"
    }
  ],
  "metadata": {
    "session_id": "sess-abc123",
    "phase_state": "interview",
    "context_mode": "new_session",
    "governor_decision": "continue_interview",
    "requested_documents": [],
    "remaining_required_documents": [],
    "turn_decision": {},
    "document_review": {},
    "prompt_trace": {},
    "runtime_view_state": {}
  }
}
```

## Verification

API 文档对应的主要测试文件：

- `tests/integration/test_simple_auth.py`
- `tests/integration/test_sessions_api.py`
- `tests/integration/test_messages_api.py`
- `tests/integration/test_files_api.py`
- `tests/integration/test_reports_api.py`
- `tests/integration/test_model_config_api.py`
- `tests/integration/test_rag_api.py`
- `tests/integration/test_openai_compat.py`
