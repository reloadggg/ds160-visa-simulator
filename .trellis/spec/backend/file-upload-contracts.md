# File Upload Contracts

> Executable contract for the Chainlit upload path in this project.

## Scenario: Chat Upload Context And Backend Hint Inference

### 1. Scope / Trigger
- Trigger: A single user message can contain both free text and attachments, and the upload flow spans `Chainlit UI -> UI client -> FastAPI /files router -> FileService -> document artifact metadata`.
- Why this needs code-spec depth: putting document-type selection logic in Chainlit causes UX drift and makes frontend behavior diverge from backend multimodal classification.
- Files in scope:
  - `.worktrees/Agent2.0/chainlit_app.py`
  - `.worktrees/Agent2.0/app/ui/chainlit_client.py`
  - `.worktrees/Agent2.0/app/api/routers/files.py`
  - `.worktrees/Agent2.0/app/services/file_service.py`

### 2. Signatures
#### Chainlit message element upload
```python
async def _upload_message_elements(message: cl.Message) -> int
```

#### UI client boundary
```python
async def ChainlitBackendClient.upload_file(
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    content_type: str = "application/octet-stream",
    document_type: str | None = None,
    context_text: str | None = None,
) -> dict[str, Any]
```

#### HTTP API boundary
```python
@router.post("", status_code=202)
async def upload_file(
    session_id: str,
    file: UploadFile = File(),
    document_type: str | None = Form(default=None),
    context_text: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict
```

#### Backend service boundary
```python
def FileService.upload(
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    content_type: str | None = None,
    document_type: str | None = None,
    context_text: str | None = None,
) -> FileUploadResult
```

### 3. Contracts
#### Request contract
- `file`: required multipart file field.
- `document_type`: optional explicit override. This is a backend input, not a required Chainlit interaction.
- `context_text`: optional raw user text from the same chat message. Pass through exactly as received; do not classify it in Chainlit.

#### Hint precedence
1. Explicit `document_type` from request form.
2. Backend-inferred hint from `context_text`.
3. Multimodal assessment candidates.
4. Filename fallback only where existing gate matching already allows it.

#### Frontend responsibility
- `chainlit_app.py` may:
  - forward attachments,
  - forward raw `message.content` as `context_text`,
  - render upload feedback.
- `chainlit_app.py` must not:
  - prompt the user to choose a document type before upload,
  - map free text into a structured `document_type`,
  - maintain its own material-type classification rules.

#### Backend responsibility
- `FileService._document_type_hint_from_context_text()` owns text-to-hint inference.
- Backend may use `context_text` as a weak prior only.
- Upload must still work when `context_text` is absent.
- `document_assessment.document_type_hint` must store the backend-resolved hint actually used for assessment.

#### Feedback contract
- `main_flow_feedback` should prefer interviewer focus when `current_focus_json.owner == "interviewer_runtime_service"` and focus kind is `required_document`.
- Gate primary document remains part of the support message when it differs from interviewer focus.
- If candidate types disagree with the resolved type, the UI copy should tell the user to describe the material in the same message, not to use frontend type-picking.

### 4. Validation & Error Matrix
| Boundary | Input / State | Expected behavior |
|----------|---------------|-------------------|
| UI client | unsupported suffix or MIME mismatch | raise `ValueError` / `UnsupportedFileTypeError` path before successful upload |
| `/v1/sessions/{session_id}/files` | missing session | `404` |
| `/v1/sessions/{session_id}/files` | file larger than 64MB | `413` |
| `/v1/sessions/{session_id}/files` | non PDF/PNG/JPG/JPEG | `415` |
| `context_text` | `None`, empty string, whitespace only | no inferred hint |
| `context_text` | matches zero or multiple required document types | no inferred hint |
| `context_text` | matches exactly one required document type keyword set | use that normalized type as `document_type_hint` |
| interviewer focus exists | uploaded doc supports current interviewer focus | `main_flow_feedback.status == "helpful"` with interviewer focus as `current_focus_document_type` |
| interviewer focus differs from gate primary | uploaded doc supports interviewer focus only | feedback still references gate primary in support message |

### 5. Good / Base / Bad Cases
#### Good
- User sends: `"这是我的 DS-2019 表。"` with `upload.pdf`.
- Chainlit forwards `context_text` unchanged.
- Backend infers `document_type_hint == "ds2019"`.
- Response persists `document_assessment.document_type_hint == "ds2019"`.

#### Base
- User uploads a file with no text.
- Chainlit sends only the multipart file.
- Backend relies on multimodal classification and existing filename/gate matching.

#### Bad
- Chainlit asks the user to choose `passport_bio` / `ds160` / `其他材料` before upload.
- Chainlit turns `"这是我的护照首页"` into `document_type="passport_bio"` on the client side.
- Result: frontend and backend classification logic drift and backend cannot audit the original user text.

### 6. Tests Required
- `tests/unit/test_chainlit_app.py`
  - assert `_upload_message_elements()` passes `context_text` through to `upload_file()`
  - assert upload-only flows no longer depend on frontend type selection
- `tests/unit/test_chainlit_client.py`
  - assert multipart body contains `context_text`
- `tests/unit/test_file_service.py`
  - assert backend infers `document_type_hint` from `context_text`
  - assert `main_flow_feedback` prefers interviewer focus over gate primary
- `tests/integration/test_files_api.py`
  - assert `/files` accepts `context_text`
  - assert returned `document_assessment.document_type_hint` reflects backend inference
- `tests/integration/test_parse_worker.py`
  - when parse has not completed yet, assert `gate_progress.overall_status == "waiting_for_parse"`
  - do not couple this test to a specific interview question strategy unless that strategy is the behavior under test

### 7. Wrong vs Correct
#### Wrong
```python
# chainlit_app.py
document_type = classify_message_text_on_frontend(message.content)
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
# chainlit_app.py
await client.upload_file(
    session_id,
    filename,
    raw_bytes,
    content_type,
    context_text=message.content,
)
```

```python
# file_service.py
document_type_hint = normalize_document_type(document_type) or self._document_type_hint_from_context_text(
    context_text,
    required_document_types=required_document_types,
)
```

## Common Mistake: Frontend Hint Drift
- Symptom: the UI asks users to select a material type or keeps its own keyword mapping.
- Why it is wrong: the backend loses the raw user text, multimodal inference and UI behavior drift apart, and tests must duplicate type-mapping rules in two places.
- Correct fix: keep `context_text` opaque until backend upload assessment.
