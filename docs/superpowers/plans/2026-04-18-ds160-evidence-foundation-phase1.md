# DS-160 Evidence Foundation Recovery Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先补齐正式 `evidence model + real parser + parse worker`，把当前“上传文件即直接改 `ApplicantProfile`”的旁路移除，并让文档必须经过解析、入库、重算后才能影响会话事实。

**Architecture:** 保持现有 `FastAPI + SQLAlchemy + SQLite` 单体，不引入 retrieval、PydanticAI 或 Chainlit。Phase 1 新增独立的文档证据领域模型、解析落库服务和单进程 parse worker；上传接口只负责持久化原始文件与排队，worker 负责解析文档、生成 `artifact/chunk/evidence` 并触发 `ApplicantProfile` 重算，从而把“材料进入系统”和“材料影响事实”这两步真正拆开。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, SQLite, PyMuPDF, python-docx, Pillow, pytesseract, pytest, uv

---

## Scope Decomposition

这次恢复工作按你确认的顺序拆成三个串行子项目：

1. **Phase 1（本计划）**：`evidence model + real parser + parse worker`，移除上传旁路。
2. **Phase 2（后续单独计划）**：`chunk/retrieval + gate state + report trace`，把证据链接到 gating 与追溯层。
3. **Phase 3（后续单独计划）**：`PydanticAI tools + Chainlit`，把 agent runtime 和 UI 接到已经稳定的证据底座上。

本文件只覆盖 **Phase 1**。原因很直接：如果 Phase 1 不先落地，Phase 2 的 retrieval/gate 和 Phase 3 的 tools/UI 只会继续依赖当前的旁路状态，不会得到真实证据链。

后续建议继续写两份计划文件：

- `docs/superpowers/plans/2026-04-18-ds160-evidence-retrieval-phase2.md`
- `docs/superpowers/plans/2026-04-18-ds160-agent-runtime-phase3.md`

## File Map

**Create:**

- `app/domain/evidence.py`
- `app/db/evidence_models.py`
- `app/repositories/evidence_repo.py`
- `app/services/document_pipeline.py`
- `app/services/profile_recompute_service.py`
- `app/workers/parse_worker.py`
- `tests/unit/test_evidence_models.py`
- `tests/unit/test_parsers.py`
- `tests/unit/test_document_pipeline.py`
- `tests/unit/test_profile_recompute_service.py`
- `tests/integration/test_parse_worker.py`

**Modify:**

- `app/main.py`
- `app/repositories/document_repo.py`
- `app/integrations/parsers.py`
- `app/services/file_service.py`
- `tests/unit/test_file_service.py`
- `tests/integration/test_files_api.py`
- `tests/integration/test_messages_api.py`

## Phase 1 Acceptance Criteria

- 上传接口不再解析文件内容，也不再直接写 `ApplicantProfile`。
- 文档解析结果以正式结构写入：
  - `DocumentRecord.artifact_json`
  - `document_chunks`
  - `evidence_items`
- `ParseWorker` 能消费 `gate_parse` 任务，并在成功后把 `DocumentRecord.status` 置为 `parsed`、`JobRecord.status` 置为 `completed`。
- `ApplicantProfile` 的文档型 provenance 来自 `EvidenceItem`，而不是 `FileService` 中的关键词旁路。
- 现有 “F1 家长资助 -> 上传资金证明 -> 继续问答” 主路径仍然成立，但必须以 “上传 -> worker 解析 -> profile 重算 -> 下一轮继续” 的方式成立。

## Task 1: 建立正式 Evidence 领域模型与持久化表

**Files:**
- Create: `app/domain/evidence.py`
- Create: `app/db/evidence_models.py`
- Create: `tests/unit/test_evidence_models.py`

- [ ] **Step 1: 先写失败测试，固定最小证据合同**

```python
# tests/unit/test_evidence_models.py
from app.domain.evidence import (
    DocumentArtifact,
    DocumentChunk,
    DocumentSourceType,
    EvidenceItem,
)


def test_evidence_item_round_trips_to_ref() -> None:
    artifact = DocumentArtifact(
        document_id="doc-1",
        session_id="sess-1",
        filename="funding_proof.pdf",
        source_type=DocumentSourceType.PDF,
        parser_name="pymupdf",
        status="parsed",
        page_count=1,
    )
    chunk = DocumentChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        session_id="sess-1",
        ordinal=0,
        page_number=1,
        text="Parent sponsor bank statement",
    )
    item = EvidenceItem(
        evidence_id="evi-1",
        session_id="sess-1",
        document_id="doc-1",
        chunk_id="chunk-1",
        evidence_type="funding_proof",
        field_path="/funding/primary_source",
        value="parents",
        excerpt=chunk.text,
    )

    assert artifact.page_count == 1
    assert item.to_ref().evidence_id == "evi-1"
    assert item.to_ref().excerpt == "Parent sponsor bank statement"
```

- [ ] **Step 2: 运行测试，确认当前仓库还没有 evidence 合同**

Run: `uv run pytest tests/unit/test_evidence_models.py -q`  
Expected: FAIL with `ModuleNotFoundError` because `app.domain.evidence` does not exist yet.

- [ ] **Step 3: 定义 `DocumentArtifact / DocumentChunk / EvidenceItem` 与对应表结构**

```python
# app/domain/evidence.py
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentSourceType(str, Enum):
    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"
    IMAGE = "image"
    UNKNOWN = "unknown"


class DocumentArtifact(BaseModel):
    document_id: str
    session_id: str
    filename: str
    source_type: DocumentSourceType
    parser_name: str
    status: str
    page_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    session_id: str
    ordinal: int
    page_number: int | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    excerpt: str


class EvidenceItem(BaseModel):
    evidence_id: str
    session_id: str
    document_id: str
    chunk_id: str
    evidence_type: str
    field_path: str
    value: str | None = None
    excerpt: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            excerpt=self.excerpt,
        )
```

```python
# app/db/evidence_models.py
from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentChunkRecord(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EvidenceItemRecord(Base):
    __tablename__ = "evidence_items"

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), index=True)
    evidence_type: Mapped[str] = mapped_column(String(64), index=True)
    field_path: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    excerpt: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
```

- [ ] **Step 4: 重新运行 evidence 合同测试**

Run: `uv run pytest tests/unit/test_evidence_models.py -q`  
Expected: PASS

- [ ] **Step 5: 提交这一层基础模型**

```bash
git add app/domain/evidence.py app/db/evidence_models.py tests/unit/test_evidence_models.py
git commit -m "feat: add evidence domain models"
```

## Task 2: 把解析器从占位实现改成真实 parser

**Files:**
- Modify: `app/integrations/parsers.py`
- Create: `tests/unit/test_parsers.py`

- [ ] **Step 1: 写失败测试，锁定 TXT/PDF/DOCX/图片 OCR 的最小行为**

```python
# tests/unit/test_parsers.py
from io import BytesIO

import fitz
from docx import Document
from PIL import Image

from app.integrations.parsers import parse_document


def build_pdf_bytes(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    return pdf.tobytes()


def build_docx_bytes(*paragraphs: str) -> bytes:
    buffer = BytesIO()
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(buffer)
    return buffer.getvalue()


def test_parse_pdf_returns_page_segments() -> None:
    parsed = parse_document("bank.pdf", build_pdf_bytes("Parent sponsor bank statement"))

    assert parsed.source_type.value == "pdf"
    assert parsed.segments[0].page_number == 1
    assert "Parent sponsor bank statement" in parsed.full_text


def test_parse_docx_returns_paragraph_segments() -> None:
    parsed = parse_document(
        "school_letter.docx",
        build_docx_bytes("University admission letter", "Program: Computer Science"),
    )

    assert parsed.source_type.value == "docx"
    assert len(parsed.segments) == 2
    assert parsed.segments[1].text == "Program: Computer Science"


def test_parse_image_uses_ocr(monkeypatch) -> None:
    image = Image.new("RGB", (320, 80), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setattr(
        "app.integrations.parsers.pytesseract.image_to_string",
        lambda image: "Parent sponsor bank statement",
    )

    parsed = parse_document("funding.png", buffer.getvalue())

    assert parsed.source_type.value == "image"
    assert parsed.segments[0].text == "Parent sponsor bank statement"
```

- [ ] **Step 2: 运行测试，确认当前解析器仍然只是 pending 文案**

Run: `uv run pytest tests/unit/test_parsers.py -q`  
Expected: FAIL because `parse_document` does not exist and current parser only returns raw strings.

- [ ] **Step 3: 实现统一 `parse_document()`，返回结构化 `ParsedDocument`**

```python
# app/integrations/parsers.py
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
from docx import Document
from PIL import Image
import pytesseract
from pydantic import BaseModel, Field

from app.domain.evidence import DocumentSourceType


class ParsedSegment(BaseModel):
    ordinal: int
    page_number: int | None = None
    text: str
    metadata: dict = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    source_type: DocumentSourceType
    parser_name: str
    segments: list[ParsedSegment]

    @property
    def full_text(self) -> str:
        return "\n".join(segment.text for segment in self.segments if segment.text).strip()


def parse_document(filename: str, raw_bytes: bytes) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        text = raw_bytes.decode("utf-8")
        return ParsedDocument(
            source_type=DocumentSourceType.TEXT,
            parser_name="plain_text",
            segments=[ParsedSegment(ordinal=0, text=text)],
        )
    if suffix == ".pdf":
        pdf = fitz.open(stream=raw_bytes, filetype="pdf")
        segments = [
            ParsedSegment(
                ordinal=index,
                page_number=index + 1,
                text=page.get_text("text").strip(),
            )
            for index, page in enumerate(pdf)
        ]
        return ParsedDocument(
            source_type=DocumentSourceType.PDF,
            parser_name="pymupdf",
            segments=segments,
        )
    if suffix == ".docx":
        document = Document(BytesIO(raw_bytes))
        segments = [
            ParsedSegment(ordinal=index, text=paragraph.text.strip())
            for index, paragraph in enumerate(document.paragraphs)
            if paragraph.text.strip()
        ]
        return ParsedDocument(
            source_type=DocumentSourceType.DOCX,
            parser_name="python-docx",
            segments=segments,
        )
    if suffix in {".png", ".jpg", ".jpeg"}:
        image = Image.open(BytesIO(raw_bytes))
        text = pytesseract.image_to_string(image).strip()
        return ParsedDocument(
            source_type=DocumentSourceType.IMAGE,
            parser_name="pytesseract",
            segments=[ParsedSegment(ordinal=0, text=text)],
        )
    return ParsedDocument(
        source_type=DocumentSourceType.UNKNOWN,
        parser_name="unsupported",
        segments=[],
    )
```

- [ ] **Step 4: 重新运行 parser 单元测试**

Run: `uv run pytest tests/unit/test_parsers.py -q`  
Expected: PASS

- [ ] **Step 5: 提交真实 parser**

```bash
git add app/integrations/parsers.py tests/unit/test_parsers.py
git commit -m "feat: implement real document parsers"
```

## Task 3: 新增 document pipeline，把 parser 结果写成 artifact/chunk/evidence

**Files:**
- Create: `app/repositories/evidence_repo.py`
- Create: `app/services/document_pipeline.py`
- Modify: `app/repositories/document_repo.py`
- Modify: `app/main.py`
- Create: `tests/unit/test_document_pipeline.py`

- [ ] **Step 1: 写失败测试，固定“处理文档后必须落库 chunk/evidence”**

```python
# tests/unit/test_document_pipeline.py
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.services.document_pipeline import DocumentPipelineService


def test_process_document_persists_chunks_and_funding_evidence(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="sess-1",
                    filename="funding_proof.txt",
                    raw_bytes=b"Parent sponsor bank statement for tuition",
                )
            )
            db.commit()

        with testing_session_local() as db:
            result = DocumentPipelineService(db).process_document("doc-1")

            chunks = db.scalars(
                select(DocumentChunkRecord).where(DocumentChunkRecord.document_id == "doc-1")
            ).all()
            evidence = db.scalars(
                select(EvidenceItemRecord).where(EvidenceItemRecord.document_id == "doc-1")
            ).all()
            document = db.get(DocumentRecord, "doc-1")

            assert result["chunk_count"] == 1
            assert result["evidence_count"] == 1
            assert document.status == "parsed"
            assert len(chunks) == 1
            assert evidence[0].field_path == "/funding/primary_source"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
```

- [ ] **Step 2: 运行测试，确认还没有 pipeline/evidence repo**

Run: `uv run pytest tests/unit/test_document_pipeline.py -q`  
Expected: FAIL because `DocumentPipelineService` and `EvidenceRepository` do not exist yet.

- [ ] **Step 3: 实现 evidence repo、document pipeline 和文档状态更新**

```python
# app/repositories/evidence_repo.py
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.domain.evidence import DocumentChunk, EvidenceItem


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def replace_document_result(
        self,
        document_id: str,
        chunks: list[DocumentChunk],
        evidence_items: list[EvidenceItem],
    ) -> None:
        self.db.execute(
            delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == document_id)
        )
        self.db.execute(
            delete(EvidenceItemRecord).where(EvidenceItemRecord.document_id == document_id)
        )
        self.db.add_all(
            [
                DocumentChunkRecord(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    session_id=chunk.session_id,
                    ordinal=chunk.ordinal,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    metadata_json=chunk.metadata,
                )
                for chunk in chunks
            ]
        )
        self.db.add_all(
            [
                EvidenceItemRecord(
                    evidence_id=item.evidence_id,
                    session_id=item.session_id,
                    document_id=item.document_id,
                    chunk_id=item.chunk_id,
                    evidence_type=item.evidence_type,
                    field_path=item.field_path,
                    value=item.value,
                    excerpt=item.excerpt,
                    confidence=item.confidence,
                    metadata_json=item.metadata,
                )
                for item in evidence_items
            ]
        )

    def list_session_evidence(self, session_id: str) -> list[EvidenceItemRecord]:
        return list(
            self.db.scalars(
                select(EvidenceItemRecord).where(EvidenceItemRecord.session_id == session_id)
            )
        )
```

```python
# app/services/document_pipeline.py
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.domain.evidence import DocumentArtifact, DocumentChunk, EvidenceItem
from app.integrations.parsers import parse_document
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository


class DocumentPipelineService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.evidence = EvidenceRepository(db)

    def process_document(self, document_id: str) -> dict[str, int]:
        document = self.documents.get_document(document_id)
        if document is None:
            raise LookupError(f"document not found: {document_id}")

        parsed = parse_document(document.filename, document.raw_bytes)
        artifact = DocumentArtifact(
            document_id=document.document_id,
            session_id=document.session_id,
            filename=document.filename,
            source_type=parsed.source_type,
            parser_name=parsed.parser_name,
            status="parsed",
            page_count=len(parsed.segments),
        )
        chunks = [
            DocumentChunk(
                chunk_id=f"chunk-{uuid4().hex[:12]}",
                document_id=document.document_id,
                session_id=document.session_id,
                ordinal=segment.ordinal,
                page_number=segment.page_number,
                text=segment.text,
                metadata=segment.metadata,
            )
            for segment in parsed.segments
            if segment.text
        ]
        evidence_items = self._extract_evidence(document.session_id, document.document_id, chunks)

        document.status = "parsed"
        document.raw_text = parsed.full_text
        document.artifact_json = artifact.model_dump(mode="json")
        self.evidence.replace_document_result(document.document_id, chunks, evidence_items)
        self.documents.save_document(document)
        self.db.flush()

        return {"chunk_count": len(chunks), "evidence_count": len(evidence_items)}

    def _extract_evidence(
        self,
        session_id: str,
        document_id: str,
        chunks: list[DocumentChunk],
    ) -> list[EvidenceItem]:
        evidence_items: list[EvidenceItem] = []
        for chunk in chunks:
            normalized = chunk.text.lower()
            if "bank statement" in normalized and ("parent" in normalized or "sponsor" in normalized):
                evidence_items.append(
                    EvidenceItem(
                        evidence_id=f"evi-{uuid4().hex[:12]}",
                        session_id=session_id,
                        document_id=document_id,
                        chunk_id=chunk.chunk_id,
                        evidence_type="funding_proof",
                        field_path="/funding/primary_source",
                        value="parents",
                        excerpt=chunk.text[:240],
                    )
                )
        return evidence_items
```

```python
# app/repositories/document_repo.py
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, JobRecord


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_document(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        raw_text: str,
        artifact_json: dict | None = None,
    ) -> DocumentRecord:
        record = DocumentRecord(
            document_id=f"doc-{uuid4().hex[:12]}",
            session_id=session_id,
            filename=filename,
            raw_bytes=raw_bytes,
            raw_text=raw_text,
            artifact_json=artifact_json or {},
        )
        self.db.add(record)
        self.db.flush()
        return record

    def enqueue_job(
        self,
        session_id: str,
        kind: str,
        payload_json: dict,
    ) -> JobRecord:
        job = JobRecord(
            job_id=f"job-{uuid4().hex[:12]}",
            session_id=session_id,
            kind=kind,
            payload_json=payload_json,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.db.get(DocumentRecord, document_id)

    def save_document(self, document: DocumentRecord) -> DocumentRecord:
        self.db.add(document)
        self.db.flush()
        return document
```

```python
# app/main.py
from app.db import evidence_models as _evidence_models  # noqa: F401
from app.db import models as _models  # noqa: F401
```

- [ ] **Step 4: 重新运行 pipeline 测试**

Run: `uv run pytest tests/unit/test_document_pipeline.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 parser 落库链路**

```bash
git add app/repositories/evidence_repo.py app/services/document_pipeline.py app/repositories/document_repo.py app/main.py tests/unit/test_document_pipeline.py
git commit -m "feat: add document pipeline and evidence persistence"
```

## Task 4: 重算 `ApplicantProfile`，并移除上传时直接改 profile 的旁路

**Files:**
- Create: `app/services/profile_recompute_service.py`
- Modify: `app/services/file_service.py`
- Modify: `app/repositories/document_repo.py`
- Create: `tests/unit/test_profile_recompute_service.py`
- Modify: `tests/unit/test_file_service.py`

- [ ] **Step 1: 写失败测试，固定“上传不改 profile，重算后才改 profile”**

```python
# tests/unit/test_profile_recompute_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.services.profile_recompute_service import ProfileRecomputeService


def test_recompute_promotes_claimed_funding_to_documented(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'profile-recompute.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            profile = ApplicantProfile.minimal("profile-sess-1")
            profile.funding["primary_source"] = "parents"
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED
            )
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id="sess-1",
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="parents",
                    excerpt="Parent sponsor bank statement",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with testing_session_local() as db:
            ProfileRecomputeService(db).recompute_session("sess-1")
            updated = ApplicantProfile.model_validate(
                db.get(SessionRecord, "sess-1").profile_json
            )

            assert updated.field_states["/funding/primary_source"].state == FieldState.DOCUMENTED
            assert updated.field_provenance["/funding/primary_source"].evidence_refs == ["evi-1"]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
```

```python
# tests/unit/test_file_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, FieldStateRecord
from app.services.file_service import FileService


def test_upload_only_enqueues_parse_job_without_mutating_profile(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-no-bypass.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            profile = ApplicantProfile.minimal("profile-sess-existing")
            profile.funding["primary_source"] = "parents"
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED
            )
            db.add(
                SessionRecord(
                    session_id="sess-existing",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                )
            )
            db.commit()

        with testing_session_local() as db:
            FileService(db).upload(
                "sess-existing",
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
            )

            updated = ApplicantProfile.model_validate(
                db.get(SessionRecord, "sess-existing").profile_json
            )
            assert updated.field_states["/funding/primary_source"].state == FieldState.CLAIMED
            assert updated.field_provenance["/funding/primary_source"].evidence_refs == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
```

- [ ] **Step 2: 运行测试，确认当前 `FileService` 仍然存在旁路**

Run: `uv run pytest tests/unit/test_profile_recompute_service.py tests/unit/test_file_service.py -q`  
Expected: FAIL because `ProfileRecomputeService` does not exist and current `FileService.upload()` still mutates `profile_json`.

- [ ] **Step 3: 实现 profile 重算，并把 `FileService.upload()` 收缩为“只入库 + 只排队”**

```python
# app/services/profile_recompute_service.py
from sqlalchemy.orm import Session

from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_repo import SessionRepository


class ProfileRecomputeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)
        self.evidence = EvidenceRepository(db)

    def recompute_session(self, session_id: str) -> ApplicantProfile:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"session not found: {session_id}")

        profile = (
            ApplicantProfile.model_validate(record.profile_json)
            if record.profile_json
            else ApplicantProfile.minimal(profile_id=f"profile-{session_id}")
        )
        profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord()

        funding_evidence = [
            item
            for item in self.evidence.list_session_evidence(session_id)
            if item.field_path == "/funding/primary_source" and item.value == "parents"
        ]
        if funding_evidence:
            profile.funding["primary_source"] = "parents"
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.DOCUMENTED
            )
            profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
                evidence_refs=[item.evidence_id for item in funding_evidence],
                source_summary="document evidence",
            )
        elif profile.funding.get("primary_source") == "parents":
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED
            )

        record.profile_json = profile.model_dump(mode="json")
        self.sessions.save(record)
        return profile
```

```python
# app/services/file_service.py
from sqlalchemy.orm import Session

from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class FileService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = DocumentRepository(db)
        self.sessions = SessionRepository(db)

    def upload(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
    ) -> tuple[str, str]:
        session_record = self.sessions.get(session_id)
        if session_record is None:
            raise SessionNotFoundError(session_id)

        try:
            document = self.repo.create_document(
                session_id=session_id,
                filename=filename,
                raw_bytes=raw_bytes,
                raw_text="",
                artifact_json={"status": "uploaded", "filename": filename},
            )
            job = self.repo.enqueue_job(
                session_id=session_id,
                kind="gate_parse",
                payload_json={"document_id": document.document_id},
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return document.document_id, job.job_id
```

```python
# app/repositories/document_repo.py
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, JobRecord


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_document(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        raw_text: str,
        artifact_json: dict | None = None,
    ) -> DocumentRecord:
        record = DocumentRecord(
            document_id=f"doc-{uuid4().hex[:12]}",
            session_id=session_id,
            filename=filename,
            raw_bytes=raw_bytes,
            raw_text=raw_text,
            artifact_json=artifact_json or {},
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.db.get(DocumentRecord, document_id)

    def save_document(self, document: DocumentRecord) -> DocumentRecord:
        self.db.add(document)
        self.db.flush()
        return document

    def enqueue_job(
        self,
        session_id: str,
        kind: str,
        payload_json: dict,
    ) -> JobRecord:
        job = JobRecord(
            job_id=f"job-{uuid4().hex[:12]}",
            session_id=session_id,
            kind=kind,
            payload_json=payload_json,
        )
        self.db.add(job)
        self.db.flush()
        return job
```

- [ ] **Step 4: 重新运行旁路移除相关测试**

Run: `uv run pytest tests/unit/test_profile_recompute_service.py tests/unit/test_file_service.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 profile 重算与 upload 解耦**

```bash
git add app/services/profile_recompute_service.py app/services/file_service.py app/repositories/document_repo.py tests/unit/test_profile_recompute_service.py tests/unit/test_file_service.py
git commit -m "feat: recompute profile from parsed evidence"
```

## Task 5: 新增 parse worker，并把现有集成回归改成“上传后必须等 worker”

**Files:**
- Create: `app/workers/parse_worker.py`
- Modify: `app/repositories/document_repo.py`
- Create: `tests/integration/test_parse_worker.py`
- Modify: `tests/integration/test_files_api.py`
- Modify: `tests/integration/test_messages_api.py`

- [ ] **Step 1: 写失败测试，固定“必须经 worker 才能解锁后续问答”**

```python
# tests/integration/test_parse_worker.py
from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.workers.parse_worker import ParseWorker


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'parse-worker.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_parse_worker_processes_upload_and_unlocks_follow_up_message(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    assert response.json()["governor_decision"] == "continue_interview"
```

- [ ] **Step 2: 运行测试，确认当前仓库没有 job 消费者**

Run: `uv run pytest tests/integration/test_parse_worker.py -q`  
Expected: FAIL because `ParseWorker` does not exist and no queued job is ever consumed.

- [ ] **Step 3: 实现 worker、job 状态迁移，并修正现有集成测试断言**

```python
# app/workers/parse_worker.py
from sqlalchemy.orm import Session

from app.repositories.document_repo import DocumentRepository
from app.services.document_pipeline import DocumentPipelineService
from app.services.profile_recompute_service import ProfileRecomputeService


class ParseWorker:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.pipeline = DocumentPipelineService(db)
        self.recompute = ProfileRecomputeService(db)

    def run_once(self) -> bool:
        job = self.documents.claim_next_job("gate_parse")
        if job is None:
            return False

        try:
            document_id = job.payload_json["document_id"]
            self.pipeline.process_document(document_id)
            self.recompute.recompute_session(job.session_id)
            job.status = "completed"
            self.db.commit()
            return True
        except Exception:
            job.status = "failed"
            self.db.commit()
            raise
```

```python
# app/repositories/document_repo.py
from sqlalchemy import select
from uuid import uuid4
from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, JobRecord


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_document(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        raw_text: str,
        artifact_json: dict | None = None,
    ) -> DocumentRecord:
        record = DocumentRecord(
            document_id=f"doc-{uuid4().hex[:12]}",
            session_id=session_id,
            filename=filename,
            raw_bytes=raw_bytes,
            raw_text=raw_text,
            artifact_json=artifact_json or {},
        )
        self.db.add(record)
        self.db.flush()
        return record

    def enqueue_job(
        self,
        session_id: str,
        kind: str,
        payload_json: dict,
    ) -> JobRecord:
        job = JobRecord(
            job_id=f"job-{uuid4().hex[:12]}",
            session_id=session_id,
            kind=kind,
            payload_json=payload_json,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.db.get(DocumentRecord, document_id)

    def save_document(self, document: DocumentRecord) -> DocumentRecord:
        self.db.add(document)
        self.db.flush()
        return document

    def claim_next_job(self, kind: str) -> JobRecord | None:
        job = self.db.scalar(
            select(JobRecord)
            .where(JobRecord.kind == kind, JobRecord.status == "queued")
            .order_by(JobRecord.job_id.asc())
        )
        if job is None:
            return None
        job.status = "processing"
        self.db.flush()
        return job
```

```python
# tests/integration/test_files_api.py
assert document.raw_text == ""
assert document.status == "uploaded"
assert job.status == "queued"
```

```python
# tests/integration/test_messages_api.py
from app.workers.parse_worker import ParseWorker
from fastapi.testclient import TestClient


def test_funding_proof_upload_allows_interview_to_continue(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert upload_response.status_code == 202
    assert response.status_code == 200
    assert response.json()["governor_decision"] == "continue_interview"
    assert response.json()["assistant_message"] == "What is the purpose of your travel?"
```

- [ ] **Step 4: 跑完新的 worker 集成链路和全量非 live 回归**

Run: `uv run pytest tests/integration/test_parse_worker.py tests/integration/test_files_api.py tests/integration/test_messages_api.py -q`  
Expected: PASS

Run: `uv run pytest -q -m "not live_llm"`  
Expected: PASS

- [ ] **Step 5: 提交 worker 与回归修正**

```bash
git add app/workers/parse_worker.py app/repositories/document_repo.py tests/integration/test_parse_worker.py tests/integration/test_files_api.py tests/integration/test_messages_api.py
git commit -m "feat: add parse worker and remove upload bypass"
```

## Out of Scope for This Plan

以下内容明确留给下一份计划，不在本文件内继续展开：

- `search_evidence()` / `get_evidence_excerpt()` / `extract_document_fields()` 等 retrieval/tool 接口
- `gate_review` 的正式状态表与阻塞逻辑
- `runtime_trace / score_history / governor_history` 的真实追溯写入
- `PydanticAI tools`、structured output agents、durable runtime
- `Chainlit` UI 接入

## Verification Commands

```bash
uv run pytest tests/unit/test_evidence_models.py tests/unit/test_parsers.py tests/unit/test_document_pipeline.py tests/unit/test_profile_recompute_service.py -q
uv run pytest tests/unit/test_file_service.py tests/integration/test_parse_worker.py tests/integration/test_files_api.py tests/integration/test_messages_api.py -q
uv run pytest -q -m "not live_llm"
```

## Self-Review

### Spec coverage

- `evidence model`：Task 1 覆盖
- `real parser`：Task 2 覆盖
- `artifact/chunk/evidence` 落库：Task 3 覆盖
- `remove upload bypass`：Task 4 覆盖
- `parse worker + recompute`：Task 5 覆盖
- Phase 2/3 内容已明确标记为后续计划，不与本计划混写

### Placeholder scan

- 无 `TODO/TBD`
- 每个任务都给出具体文件、测试、命令与实现片段
- 没有使用“类似 Task N”这类跳转式描述

### Type consistency

- Phase 1 统一使用 `DocumentArtifact / DocumentChunk / EvidenceItem`
- `ApplicantProfile.field_provenance.evidence_refs` 继续存 `evidence_id` 字符串，保持与现有 profile 合同兼容
- retrieval 与更丰富的 `EvidenceRef` 暴露留到 Phase 2，再做对外 contract 扩展
