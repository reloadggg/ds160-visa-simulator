# Multimodal Upload Contracts

## Scenario: model-first upload assessment with user correction

### 1. Scope / Trigger

- Trigger：修改上传识别、document type 候选、文件 API、Chainlit 上传反馈、artifact_json 或 gate 反馈语义
- 关键文件：
  - `app/services/multimodal_extraction_service.py`
  - `app/services/file_service.py`
  - `app/api/routers/files.py`
  - `chainlit_app.py`

### 2. Signatures

```python
MultimodalExtractionService.extract(
    *,
    filename: str,
    raw_bytes: bytes,
    source_type: DocumentSourceType,
    document_type: str | None,
) -> MultimodalExtractionResult | None

MultimodalExtractionService.assess_document(
    *,
    filename: str,
    raw_bytes: bytes,
    source_type: DocumentSourceType,
    document_type_hint: str | None = None,
) -> MultimodalUploadAssessment

FileService.upload(
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    content_type: str | None = None,
    document_type: str | None = None,
) -> FileUploadResult
```

### 3. Contracts

#### 3.1 Upload assessment output

`app/services/multimodal_extraction_service.py::MultimodalUploadAssessment`

```json
{
  "document_type_candidates": [
    {
      "document_type": "funding_proof",
      "confidence": 0.65
    }
  ],
  "relevance": "high | medium | low | unknown",
  "supported_claims": ["/funding/primary_source"],
  "confidence": 0.65
}
```

规则：

- 候选类型由模型先给出，前端/用户可以纠偏
- `supported_claims` 表示该文件能支撑的字段或主张，不再只给布尔 `relevant`
- `confidence` 表示 assessment 总体置信度，不等于 field extraction 的单字段置信度

#### 3.2 Document type resolution priority

`app/services/file_service.py::FileService.upload`

优先级固定为：

1. 用户显式指定的 `document_type`
2. assessment 与当前 required documents 对齐后的 `supported_document_type`
3. `document_type_candidates` 第一候选

不要把“前端未提前选择 document_type”当成阻塞条件。

#### 3.3 Stored artifact contract

写入 `DocumentRecord.artifact_json` 的字段至少包括：

```json
{
  "status": "uploaded",
  "filename": "funding_proof.pdf",
  "document_type": "funding_proof",
  "document_type_hint": null,
  "document_type_candidates": ["funding_proof"],
  "relevance": "medium",
  "supported_claims": ["/funding/primary_source"],
  "confidence": 0.65,
  "feedback_message": "string | null",
  "relevant": true
}
```

如有主流程反馈，再追加：

```json
{
  "main_flow_feedback": {
    "status": "helpful | partial_helpful | not_helpful",
    "supported_document_type": "funding_proof",
    "current_focus_document_type": "ds160",
    "message": "string"
  }
}
```

#### 3.4 Files API response contract

`POST /v1/sessions/{session_id}/files`

返回字段至少包括：

```json
{
  "document_id": "doc-...",
  "document_status": "uploaded",
  "job_id": "job-...",
  "job_status": "queued",
  "document_type": "funding_proof",
  "document_type_candidates": ["funding_proof"],
  "relevance": "medium",
  "supported_claims": ["/funding/primary_source"],
  "confidence": 0.65,
  "feedback_message": "string | null",
  "relevant": true,
  "main_flow_feedback": {},
  "requested_documents": [],
  "gate_progress": {}
}
```

前端契约：

- Chainlit 必须展示候选类型、相关性、支持主张与反馈文案
- 前端不允许只消费旧的 `relevant: bool`

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| 支持的 PDF / 图片 | `pdf/png/jpg/jpeg` | 允许上传并入队 parse job | files API 202 |
| MIME 与扩展名冲突 | `content_type=pdf` 但后缀 `.png` | 拒绝，返回 415 | `UnsupportedFileTypeError` |
| 文档类型不支持抽取 | `document_type=bank_statement` | `extract()` 返回 `None`；assessment 走启发式或候选 | unit tests |
| 模型未启用 | 未配置 live model | `extract()` 返回 `None`；`assess_document()` 降级到启发式 | `tests/unit/test_file_service.py` |
| 模型调用成功 | multimodal 响应合法 | `extract()` 必须返回结果，不能吞成 `None` | `tests/unit/test_multimodal_extraction_service.py` |
| 用户未手填类型 | `document_type=None` | 仍返回 `document_type_candidates` 与 `supported_claims` | files API / Chainlit |

### 5. Good/Base/Bad Cases

#### Good

- funding proof 上传后返回 `document_type_candidates=["funding_proof"]`
- Chainlit 直接展示“候选类型 + 支持主张 + 当前主流程反馈”
- parse 前后允许主线继续推进到下一个缺口，而不是死锁在“用户没先选类型”

#### Base

- 模型不可用时，仍能通过启发式候选给前端最小可纠偏信息
- `document_type_hint` 存在时，优先尝试按 hint 解析；解析不可用再降级

#### Bad

- 上传链路只返回 `relevant: bool`，不返回候选类型和支持主张
- 强迫用户在上传前先准确选择唯一 `document_type`
- `extract()` 成功拿到模型响应却仍返回 `None`

### 6. Tests Required

- `tests/unit/test_multimodal_extraction_service.py`
  - 断言图片/PDF 均能构造 multimodal payload，并正确解析结果
- `tests/unit/test_file_service.py`
  - 断言 resolved document type 优先级、artifact_json 字段与 feedback
- `tests/integration/test_files_api.py`
  - 断言 files API response 字段完整
- `tests/unit/test_chainlit_app.py`
  - 断言 Chainlit 能显示候选类型、相关性、支持主张
- `tests/integration/live/test_live_messages_api.py`
  - 断言上传并解析后主线至少不会继续卡在旧的 `funding_proof`

### 7. Wrong vs Correct

#### Wrong

```python
return {
    "relevant": True,
}
```

```python
resolved_document_type = normalize_document_type(document_type)
if resolved_document_type is None:
    raise ValueError("document_type is required")
```

#### Correct

```python
return {
    "document_type_candidates": ["funding_proof"],
    "relevance": "medium",
    "supported_claims": ["/funding/primary_source"],
    "confidence": 0.65,
}
```

```python
resolved_document_type = (
    normalize_document_type(document_type)
    or supported_document_type
    or top_assessment_document_type
)
```
