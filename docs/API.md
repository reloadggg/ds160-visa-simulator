# DS-160 API Reference

本文件记录当前 FastAPI 后端对外暴露的主要接口。代码入口位于 `app/api/routers/`，默认前缀为 `/v1`；前端在生产镜像中通过 `/api` 反向代理访问后端，因此浏览器侧实际 URL 通常是 `/api/v1/...`。

## Product State Model

当前 API 的主产品状态是 Case Memory / Case Board，而不是 Gate 材料清单。

- 上传材料会创建 `case_understanding` 任务，材料理解结果写入 Case Memory。
- 用户对话中的明确事实陈述也可以作为 `source_type=user_turn` 写入 Case Memory。
- `case_board` / `case_board_delta` 用于展示事实、证据、证明点、冲突和下一问原因。
- `gate_progress`、`requested_documents`、`remaining_required_documents` 仍保留给旧前端/API 消费者，但只能视为兼容投影。
- 除 `family_not_selected` 外，材料缺失、案例理解处理中或 Gate 未 ready 不阻断聊天。

核心 Case Board shape：

```json
{
  "schema_version": "case_board.v1",
  "latest_material": {
    "document_id": "doc-abc123",
    "filename": "i20.png",
    "understanding_status": "completed",
    "document_type": "i20",
    "confidence": 0.86
  },
  "claims": [
    {
      "claim_id": "claim-doc-abc123-school-name",
      "field_path": "/education/school_name",
      "value": "Example University",
      "status": "documented",
      "supporting_evidence_ids": ["ev-doc-abc123-school-name"],
      "conflicting_evidence_ids": []
    }
  ],
  "evidence_cards": [
    {
      "evidence_id": "ev-doc-abc123-school-name",
      "source_type": "uploaded_file",
      "document_id": "doc-abc123",
      "page_number": 1,
      "excerpt": "School Name: Example University",
      "claim_refs": ["claim-doc-abc123-school-name"],
      "confidence": 0.86
    }
  ],
  "proof_points": [
    {
      "proof_point_id": "proof-doc-abc123-school",
      "visa_family": "f1",
      "question": "Does the case document the school and program?",
      "status": "supported",
      "why_it_matters": "F-1 interview questions depend on the school and program context.",
      "claim_refs": ["claim-doc-abc123-school-name"],
      "evidence_refs": ["ev-doc-abc123-school-name"]
    }
  ],
  "conflicts": [],
  "next_move": {
    "move_type": "ask",
    "question": "为什么选择这所学校？",
    "reason": "I-20 已提供学校信息，下一步核验学习动机。",
    "claim_refs": ["claim-doc-abc123-school-name"],
    "evidence_refs": ["ev-doc-abc123-school-name"]
  }
}
```

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

### `POST /v1/sessions/{session_id}/debug/material-bundles`

生成一整套 synthetic 调试材料，并触发材料变更后的主流程刷新。默认关闭，需要 `ALLOW_DEBUG_FILL=true`；不要在公开生产环境开启。

材料正文模拟真实文件/OCR 文本，例如 DS-160 确认页、护照首页、I-20、录取信、银行证明和亲属关系证明；字段缺陷只通过材料之间的值差异、金额缺口、资金链证据缺口或用户 claim 与材料不一致体现。

Request:

```json
{
  "scenario": "school_mismatch_bundle",
  "include_synthetic_user_turns": true
}
```

支持的 `scenario`：

- `normal_f1_bundle`
- `school_mismatch_bundle`
- `identity_mismatch_bundle`
- `funding_shortfall_bundle`
- `sponsor_chain_gap_bundle`
- `claim_vs_document_bundle`

Response shape:

```json
{
  "session_id": "sess-abc123",
  "bundle_id": "dbg-bundle-abc123",
  "scenario": "school_mismatch_bundle",
  "scenario_label": "学校材料冲突包",
  "documents": [
    {
      "document_id": "doc-abc123",
      "filename": "debug_i20.txt",
      "document_type": "i20",
      "document_type_label": "I-20",
      "raw_text": "U.S. Department of Homeland Security\nCertificate of Eligibility for Nonimmigrant Student Status (F-1)...",
      "fields": {
        "/education/school_name": "Example University"
      },
      "content_url": "/v1/sessions/sess-abc123/files/doc-abc123/content"
    }
  ],
  "synthetic_turns": [],
  "expected_findings": [
    {
      "kind": "cross_document_conflict",
      "description": "I-20 and admission letter contain different school names.",
      "field_path": "/education/school_name",
      "document_types": ["i20", "admission_letter"],
      "severity": "high",
      "visible_to_model": false
    }
  ],
  "assistant_message": "string",
  "governor_decision": "high_risk_review",
  "requested_documents": [],
  "remaining_required_documents": [],
  "turn_decision": {},
  "document_review": {},
  "runtime_view_state": {},
  "phase_state": "interview",
  "gate_status": {},
  "main_flow_refresh_error": null
}
```

测试参考隔离规则：

- `expected_findings` 只给 API 调试响应和前端材料详情里的“核验线索”展示。
- `expected_findings`、`*_bundle` 场景名、bundle id 不进入 document review prompt/context。
- `DocumentRecord.raw_text`、`DocumentChunk.text`、`EvidenceItem.excerpt` 不应包含 `Issue:`、`Missing:`、`Expected:`、`Defect:` 或 `This conflicts with` 这类答案提示。
- document review 必须基于材料字段、材料正文和用户 claim 自行识别缺陷。

### `POST /v1/sessions/{session_id}/debug/material-bundles/stream`

材料包生成的事件式 SSE 入口。前端应在收到首个事件后立即显示 pending/进度状态，并在 `final` 后写入材料库。

Request 与非流式接口相同。

事件类型：

- `accepted`：请求已接收
- `debug_bundle_started`：材料包开始生成，包含 `bundle_id`、`scenario`、`document_count`
- `document_created`：单份材料已写入 `DocumentRecord`
- `evidence_written`：材料字段和 evidence/chunk 已写入
- `profile_recomputed`：profile 已根据材料重算
- `gate_refreshed`：最低材料包状态已刷新
- `document_review_started`：开始触发材料变更后的主流程刷新
- `governor_decided`：主流程刷新已产出 governor/turn decision 摘要
- `final`：完整 `DebugMaterialBundleResponse`
- `error`：`{"status": 404|422|500, "detail": "..."}`，流内错误事件

SSE 响应头包含：

```http
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

线上 Nginx/反向代理还需要关闭 `/api/` 的响应缓冲，否则浏览器可能无法逐事件更新 UI。

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
  "runtime_view_state": {},
  "phase_state": "interview"
}
```

Runtime notes:

- 用户可见的 `assistant_message` 只能来自 graph adjudication agent 或 deterministic safe fallback。
- 材料理解、Case Memory 更新、Governor 和 guard 不会额外写第二条用户可见主回复。
- 当 `AGENT_RUNTIME=graph` 时，主流程经 `GraphRuntimeAdapter` / LangGraph 运行。
- typed adjudication 模型不可用时，fallback 会读取 `case_board.next_move` 和 `case_memory.conflicts`，不会退回“先补齐材料”的固定话术。

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
未配置多模态模型时，后端不会根据文件名猜材料类型；文件名只作为审计元数据。

上传语义：

- 新上传只 enqueue `kind="case_understanding"`。
- 历史 `gate_parse` job 只作为 worker 兼容 fallback 继续消费。
- 图片上传不调用 OCR；图片 parser 只标记 `source_type=image` 和 `parser_name=multimodal_required`。
- PDF/图片的视觉理解由 `MaterialUnderstandingService` 经多模态模型完成。
- 上传完成后聊天可继续，不需要等待“材料齐”或“解析完成”。

Response:

```json
{
  "document_id": "doc-abc123",
  "content_url": "/v1/sessions/sess-abc123/files/doc-abc123/content",
  "document_status": "uploaded",
  "job_id": "job-123",
  "job_status": "queued",
  "understanding_status": "queued",
  "document_type": null,
  "document_assessment": {
    "document_type": null,
    "document_type_hint": null,
    "document_type_candidates": [],
    "relevance": "unknown",
    "supported_claims": [],
    "confidence": 0,
    "feedback_message": null,
    "relevant": null
  },
  "document_type_candidates": [],
  "relevance": "unknown",
  "supported_claims": [],
  "confidence": 0,
  "feedback_message": null,
  "relevant": null,
  "main_flow_feedback": {},
  "case_board_delta": {
    "latest_material": {
      "document_id": "doc-abc123",
      "filename": "i20.png",
      "understanding_status": "queued",
      "document_type": null,
      "document_type_candidates": [],
      "relevance": "unknown",
      "supported_claims": [],
      "confidence": 0,
      "unknowns": [
        "案例理解任务已创建，视觉材料的完整证据、冲突和追问建议仍在更新。"
      ]
    },
    "evidence_cards": [],
    "claims": [],
    "open_proof_points": [],
    "conflicts": [],
    "next_move": {
      "move_type": "ask",
      "question": "请继续回答面签问题；材料理解完成后我会结合证据调整追问。",
      "reason": "文件已保存并进入案例理解队列，当前无需等待材料齐套。",
      "claim_refs": [],
      "evidence_refs": []
    }
  },
  "evidence_cards": [],
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

### `DELETE /v1/sessions/{session_id}/files/{document_id}`

撤回一份已上传材料。接口不会物理删除审计记录，而是写入 Case Memory tombstone，
并从 Case Board / replay / Gate compatibility projection 中排除该材料贡献。

Response:

```json
{
  "document_id": "doc-abc123",
  "document_status": "tombstoned",
  "case_board": {
    "schema_version": "case_board.v1",
    "claims": [],
    "evidence_cards": [],
    "proof_points": [],
    "conflicts": [],
    "next_move": null
  }
}
```

## Reports API

### `GET /v1/sessions/{session_id}/reports/user`

返回面向用户的准备建议报告。
报告会携带 `case_board`，并从 Case Memory 中的 documented / stated /
contradicted facts、proof points 和 conflicts 生成优势、薄弱证明点和风险提示。

Response excerpt:

```json
{
  "session_id": "sess-abc123",
  "summary": "string",
  "risk_level": "medium",
  "case_board": {
    "schema_version": "case_board.v1",
    "claims": [],
    "evidence_cards": [],
    "proof_points": [],
    "conflicts": []
  },
  "strengths": [],
  "weaknesses": [],
  "allowed_next_actions": []
}
```

### `GET /v1/sessions/{session_id}/reports/internal`

返回内部调试报告，包含 runtime ledger、trace、score history 和 governor history。
同时返回当前 `case_board`，用于调试报告结论的证据来源。

### `POST /v1/sessions/{session_id}/reports/review`

生成面试复盘报告。

### `GET /v1/sessions/{session_id}/reports/export`

导出当前会话快照，包含会话、用户报告、内部报告、profile snapshot 和材料摘要。用户报告和内部报告里都会包含当前 `case_board`；被 tombstone 的材料不会继续贡献 Case Board 事实或证据。

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
- `tests/integration/test_debug_material_bundles_api.py`
- `tests/integration/test_messages_api.py`
- `tests/integration/test_files_api.py`
- `tests/integration/test_reports_api.py`
- `tests/integration/test_model_config_api.py`
- `tests/integration/test_rag_api.py`
- `tests/integration/test_openai_compat.py`
