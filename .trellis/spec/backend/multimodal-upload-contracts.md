# Multimodal Upload Contracts

## Scenario: model-first upload assessment with user correction

### 1. Scope / Trigger

- Trigger：修改上传识别、document type 候选、文件 API、Chainlit 上传反馈、artifact_json 或 gate 反馈语义
- 关键文件：
  - `app/ui/chainlit_client.py`
  - `app/services/multimodal_extraction_service.py`
  - `app/services/file_service.py`
  - `app/api/routers/files.py`
  - `chainlit_app.py`

### 2. Signatures

```python
ChainlitBackendClient.upload_file(
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    content_type: str = "application/octet-stream",
    document_type: str | None = None,
    context_text: str | None = None,
) -> dict[str, Any]

upload_file(
    session_id: str,
    file: UploadFile = File(),
    document_type: str | None = Form(default=None),
    context_text: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict

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
    context_text: str | None = None,
) -> FileUploadResult
```

### 3. Contracts

#### 3.0 Request contract

- `file` 是必填 multipart 字段。
- `document_type` 是可选显式覆盖，属于后端输入，不应成为 Chainlit 必经交互。
- `context_text` 是同一条聊天消息里的原始用户文本，Chainlit 只允许原样透传，不允许先把它解析成结构化类型。

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
- `document_type_hint` 可以来自显式 `document_type`，也可以来自后端基于 `context_text` 的弱先验推断

#### 3.2 Document type resolution priority

`app/services/file_service.py::FileService.upload`

优先级固定为：

1. 用户显式指定的 `document_type`
2. 后端基于 `context_text` 推断出的 `document_type_hint`
3. assessment 与当前 required documents 对齐后的 `supported_document_type`
4. `document_type_candidates` 第一候选

不要把“前端未提前选择 document_type”当成阻塞条件。
不要在 Chainlit 里复写一套 `context_text -> document_type` 的映射规则。

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
- Chainlit 必须把同一条消息里的 `message.content` 作为 `context_text` 透传给 `/files`
- Chainlit 不允许在上传前弹出 document type 选择器，也不允许把自由文本先解析成 `document_type`

#### 3.5 Main flow feedback priority

- 若 `current_focus_json.owner == "interviewer_runtime_service"` 且 `kind == "required_document"`，`main_flow_feedback.current_focus_document_type` 必须优先采用 interviewer focus。
- gate primary document 仍可作为 support message 出现在反馈里，用于说明门控层当前缺口。
- 当候选类型与 resolved type 不一致时，反馈文案只能提示“在同一条消息里说明材料类型，让后端结合文本纠偏”，不能把纠偏责任推回前端强制选择。

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| 支持的 PDF / 图片 | `pdf/png/jpg/jpeg` | 允许上传并入队 parse job | files API 202 |
| MIME 与扩展名冲突 | `content_type=pdf` 但后缀 `.png` | 拒绝，返回 415 | `UnsupportedFileTypeError` |
| 文档类型不支持抽取 | `document_type=bank_statement` | `extract()` 返回 `None`；assessment 走启发式或候选 | unit tests |
| 模型未启用 | 未配置 live model | `extract()` 返回 `None`；`assess_document()` 降级到启发式 | `tests/unit/test_file_service.py` |
| 模型调用成功 | multimodal 响应合法 | `extract()` 必须返回结果，不能吞成 `None` | `tests/unit/test_multimodal_extraction_service.py` |
| 用户未手填类型 | `document_type=None` | 仍返回 `document_type_candidates` 与 `supported_claims` | files API / Chainlit |
| `context_text` 为空 | `None` / 空串 / 全空白 | 不推断 hint，继续走模型候选与已有 gate 匹配 | `tests/unit/test_file_service.py` |
| `context_text` 命中唯一 required document | `"这是我的 DS-2019 表。"` | `document_assessment.document_type_hint == "ds2019"` | `tests/unit/test_file_service.py` / `tests/integration/test_files_api.py` |
| interviewer focus 与 gate primary 不同 | interviewer 要 `ds2019`，gate 仍缺 `ds160` | `main_flow_feedback` 以 interviewer focus 为主，但 support message 保留 `ds160` 缺口 | `tests/unit/test_file_service.py` / `tests/integration/test_files_api.py` |

### 5. Good/Base/Bad Cases

#### Good

- funding proof 上传后返回 `document_type_candidates=["funding_proof"]`
- Chainlit 直接展示“候选类型 + 支持主张 + 当前主流程反馈”
- 用户在同一条消息里写“这是我的护照首页”，Chainlit 原样透传文本，后端自己推断 `passport_bio`
- parse 前后允许主线继续推进到下一个缺口，而不是死锁在“用户没先选类型”

#### Base

- 模型不可用时，仍能通过启发式候选给前端最小可纠偏信息
- `document_type_hint` 存在时，优先尝试按 hint 解析；解析不可用再降级
- 没有 `context_text` 也必须能正常上传，hint 不是前置必填步骤

#### Bad

- 上传链路只返回 `relevant: bool`，不返回候选类型和支持主张
- 强迫用户在上传前先准确选择唯一 `document_type`
- 前端根据 `"这是我的护照首页"` 直接构造 `document_type="passport_bio"` 再上传
- `extract()` 成功拿到模型响应却仍返回 `None`

### 6. Tests Required

- `tests/unit/test_multimodal_extraction_service.py`
  - 断言图片/PDF 均能构造 multimodal payload，并正确解析结果
- `tests/unit/test_chainlit_client.py`
  - 断言 multipart 请求体包含 `context_text`
- `tests/unit/test_file_service.py`
  - 断言 resolved document type 优先级、artifact_json 字段、`context_text` hint 与 feedback
- `tests/integration/test_files_api.py`
  - 断言 files API response 字段完整，且支持 `context_text`
- `tests/unit/test_chainlit_app.py`
  - 断言 Chainlit 透传 `context_text`，不再强制前端选类型
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

```python
# chainlit_app.py
document_type = infer_document_type_from_message(message.content)
await client.upload_file(
    session_id,
    filename,
    raw_bytes,
    content_type,
    document_type=document_type,
)
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
    or self._document_type_hint_from_context_text(
        context_text,
        required_document_types=required_document_types,
    )
    or supported_document_type
    or top_assessment_document_type
)
```

```python
await client.upload_file(
    session_id,
    filename,
    raw_bytes,
    content_type,
    context_text=message.content,
)
```

## Scenario: standardized document assessment with compatibility mirrors

### 1. Scope / Trigger

- Trigger：修改 `DocumentAssessment`、上传 artifact 写入、document pipeline 透传、files API 响应或 gate/document 消费逻辑
- 关键文件：
  - `app/domain/evidence.py`
  - `app/services/file_service.py`
  - `app/services/document_pipeline.py`
  - `app/services/gate_runtime_service.py`
  - `app/api/routers/files.py`
  - `chainlit_app.py`

### 2. Signatures

```python
DocumentAssessment.from_artifact(
    artifact_json: dict[str, Any] | None,
) -> DocumentAssessment

DocumentAssessment.to_metadata_payload() -> dict[str, Any]

FileService.upload(
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    content_type: str | None = None,
    document_type: str | None = None,
) -> FileUploadResult

DocumentPipelineService.process_document(document_id: str) -> dict[str, int]
```

### 3. Contracts

#### 3.1 Standardized nested assessment contract

`app/domain/evidence.py::DocumentAssessment`

```json
{
  "document_type": "funding_proof",
  "document_type_hint": null,
  "document_type_candidates": ["funding_proof"],
  "relevance": "medium",
  "supported_claims": ["/funding/primary_source"],
  "confidence": 0.65,
  "feedback_message": "string | null",
  "relevant": true,
  "counts_toward_gate": true,
  "main_flow_feedback": {
    "status": "helpful | partial_helpful | not_helpful",
    "supported_document_type": "funding_proof",
    "current_focus_document_type": "funding_proof",
    "message": "string"
  }
}
```

读取优先级：

1. `artifact_json["document_assessment"]`
2. `artifact_json["metadata"]["document_assessment"]`
3. 旧 root-level mirror 字段
4. 旧 metadata root 字段

规则：

- 标准化 nested shape 优先于旧镜像字段
- `from_artifact()` 必须能同时读取新旧两种形状
- `to_metadata_payload()` 必须输出可直接放入 API/metadata/artifact 的 JSON 结构

#### 3.2 Compatibility mirror write contract

上传写入 `artifact_json` 时，必须同时保留：

- root-level mirror 字段：
  - `document_type`
  - `document_type_hint`
  - `document_type_candidates`
  - `relevance`
  - `supported_claims`
  - `confidence`
  - `feedback_message`
  - `relevant`
  - `counts_toward_gate`（如有）
  - `main_flow_feedback`（如有）
- 标准 nested 字段：
  - `document_assessment`

原因：

- files API / Chainlit / gate runtime 仍有兼容期消费者
- 但新的主断言必须优先走 `DocumentAssessment.from_artifact(...)`

#### 3.3 Pipeline preservation contract

`DocumentPipelineService.process_document()` 必须把上传阶段 assessment 透传到解析后 artifact：

- `DocumentArtifact.metadata["document_assessment"]`
- `DocumentArtifact.metadata["document_type"]`
- 如存在以下字段，也必须保留：
  - `counts_toward_gate`
  - `feedback_message`
  - `relevant`
  - `main_flow_feedback`

规则：

- parse 阶段不允许把上传阶段 assessment 丢掉
- parse 阶段可以更新 `document_type`，但必须写回 standardized assessment

#### 3.4 Files API response contract

`POST /v1/sessions/{session_id}/files` 在兼容期必须同时返回：

```json
{
  "document_type": "funding_proof",
  "document_assessment": {
    "document_type": "funding_proof"
  },
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

规则：

- `document_assessment` 是主合同
- 旧平铺字段暂时保留，直到所有消费者完成迁移
- `main_flow_feedback` 与 `document_assessment.main_flow_feedback` 必须一致

### 4. Validation & Error Matrix

| Scenario | Input | Expected Behavior | Assertion Point |
|----------|-------|-------------------|-----------------|
| 只有旧 artifact shape | root-level 字段，无 nested | `from_artifact()` 仍能正确解析 | `tests/unit/test_document_assessment_contract.py` |
| 新旧字段同时存在 | nested 与 mirror 冲突 | nested standardized shape 优先 | `tests/unit/test_document_assessment_contract.py` |
| 上传后尚无主流程反馈 | `main_flow_feedback=None` | API 和 artifact 仍包含 `document_assessment`，但可省略 `main_flow_feedback` | `tests/unit/test_file_service.py` |
| 上传后拿到主流程反馈 | `main_flow_feedback` 存在 | root-level 与 nested 两处都同步 | `tests/integration/test_files_api.py` |
| document pipeline 重写 artifact | parse 后写 `DocumentArtifact` | 不能丢失 upload assessment | `tests/unit/test_document_pipeline.py` |
| gate runtime 读 document type | 解析后 artifact | 应通过 `DocumentAssessment.from_artifact(...)` 读取主合同 | `tests/unit/test_gate_runtime_service.py` |

### 5. Good/Base/Bad Cases

#### Good

- `FileService.upload()` 返回 `document_assessment`，同时保留平铺字段镜像
- `DocumentPipelineService.process_document()` 继续透传 upload assessment
- files API 与 Chainlit 都优先消费 `document_assessment`

#### Base

- 旧 artifact 仍可被读取，但新测试主断言改为 `DocumentAssessment.from_artifact(...)`
- `counts_toward_gate` 没有值时可以省略，不强行写 `null`

#### Bad

- 只写平铺字段，不写 `document_assessment`
- 解析后 artifact 重新覆盖掉上传阶段 assessment
- API 返回里 `main_flow_feedback` 和 nested assessment 内的 `main_flow_feedback` 不一致
- gate/document consumer 继续手写一套字段优先级，而不复用 `from_artifact()`

### 6. Tests Required

- `tests/unit/test_document_assessment_contract.py`
  - 断言新旧 shape 的解析优先级
- `tests/unit/test_file_service.py`
  - 断言 upload 写入 mirror + nested 双形状
- `tests/unit/test_document_pipeline.py`
  - 断言 parse 后仍保留 standardized assessment
- `tests/unit/test_gate_runtime_service.py`
  - 断言 gate 逻辑通过 `DocumentAssessment.from_artifact(...)` 读取类型和 gate 计数
- `tests/integration/test_files_api.py`
  - 断言 files API 同时返回 nested assessment 与兼容平铺字段
- `tests/unit/test_chainlit_app.py`
  - 断言上传反馈优先消费 `document_assessment`

### 7. Wrong vs Correct

#### Wrong

```python
artifact_json = {
    "document_type": resolved_document_type,
    "relevance": assessment.relevance,
}
```

```python
document_type = document.artifact_json.get("document_type")
```

#### Correct

```python
artifact_json = {
    "document_type": document_assessment.document_type,
    "relevance": document_assessment.relevance,
    "document_assessment": document_assessment.to_metadata_payload(),
}
```

```python
assessment = DocumentAssessment.from_artifact(document.artifact_json)
document_type = assessment.document_type
```
