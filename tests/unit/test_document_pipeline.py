from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.domain.evidence import DocumentSourceType
from app.repositories.document_repo import DocumentRepository
from app.services.document_pipeline import DocumentPipelineService
from app.services.multimodal_extraction_service import (
    MultimodalExtractedField,
    MultimodalExtractionResult,
)


def build_pdf_bytes(*pages: str) -> bytes:
    import fitz

    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


def test_process_document_persists_artifact_chunk_and_evidence(tmp_path) -> None:
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
            db.commit()

            document = DocumentRepository(db).get_document("doc-1")
            chunks = db.scalars(select(DocumentChunkRecord)).all()
            evidence = db.scalars(select(EvidenceItemRecord)).all()

            assert result["chunk_count"] == 1
            assert result["evidence_count"] == 1
            assert document is not None
            assert document.status == "parsed"
            assert document.raw_text == "Parent sponsor bank statement for tuition"
            assert document.artifact_json["status"] == "parsed"
            assert document.artifact_json["parser_name"] == "plain_text"
            assert document.artifact_json["source_type"] == "text"
            assert document.artifact_json["page_count"] == 1
            assert len(chunks) == 1
            assert chunks[0].text == "Parent sponsor bank statement for tuition"
            assert len(evidence) == 1
            assert evidence[0].field_path == "/funding/primary_source"
            assert evidence[0].excerpt == "Parent sponsor bank statement for tuition"

            document.raw_bytes = b"Sponsor bank statement updated"
            db.commit()

            result = DocumentPipelineService(db).process_document("doc-1")
            db.commit()

            refreshed_document = DocumentRepository(db).get_document("doc-1")
            refreshed_chunks = db.scalars(select(DocumentChunkRecord)).all()
            refreshed_evidence = db.scalars(select(EvidenceItemRecord)).all()

            assert result["chunk_count"] == 1
            assert result["evidence_count"] == 1
            assert refreshed_document is not None
            assert refreshed_document.raw_text == "Sponsor bank statement updated"
            assert refreshed_document.artifact_json["status"] == "parsed"
            assert len(refreshed_chunks) == 1
            assert refreshed_chunks[0].text == "Sponsor bank statement updated"
            assert len(refreshed_evidence) == 1
            assert refreshed_evidence[0].excerpt == "Sponsor bank statement updated"
            assert refreshed_evidence[0].chunk_id == refreshed_chunks[0].chunk_id
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_document_marks_unsupported_parser_output(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-unsupported.sqlite3'}",
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
                    filename="unsupported.xyz",
                    raw_bytes=b"ignored",
                )
            )
            db.commit()

        with testing_session_local() as db:
            result = DocumentPipelineService(db).process_document("doc-1")
            db.commit()

            document = DocumentRepository(db).get_document("doc-1")
            chunks = db.scalars(select(DocumentChunkRecord)).all()
            evidence = db.scalars(select(EvidenceItemRecord)).all()

            assert result["chunk_count"] == 0
            assert result["evidence_count"] == 0
            assert document is not None
            assert document.status == "unsupported"
            assert document.raw_text == ""
            assert document.artifact_json["status"] == "unsupported"
            assert document.artifact_json["parser_name"] == "unsupported"
            assert document.artifact_json["source_type"] == "unknown"
            assert document.artifact_json["page_count"] == 0
            assert chunks == []
            assert evidence == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_passport_and_i20_extract_structured_evidence(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-structured.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add_all(
                [
                    DocumentRecord(
                        document_id="doc-passport",
                        session_id="sess-1",
                        filename="passport_bio.txt",
                        raw_bytes=(
                            b"Full Name: Ada Lovelace\n"
                            b"Passport Number: P1234567\n"
                            b"Nationality: UK"
                        ),
                    ),
                    DocumentRecord(
                        document_id="doc-i20",
                        session_id="sess-1",
                        filename="i20.txt",
                        raw_bytes=(
                            b"SEVIS ID: N1234567890\n"
                            b"School Name: Example University\n"
                            b"Program: Computer Science"
                        ),
                    ),
                ]
            )
            db.commit()

        with testing_session_local() as db:
            DocumentPipelineService(db).process_document("doc-passport")
            DocumentPipelineService(db).process_document("doc-i20")
            db.commit()

            evidence = db.scalars(
                select(EvidenceItemRecord).order_by(EvidenceItemRecord.evidence_id)
            ).all()
            extracted = {(item.evidence_type, item.field_path): item.value for item in evidence}

            assert extracted[("passport_bio", "/identity/full_name")] == "Ada Lovelace"
            assert extracted[("passport_bio", "/identity/passport_number")] == "P1234567"
            assert extracted[("passport_bio", "/identity/nationality")] == "UK"
            assert extracted[("i20", "/education/sevis_id")] == "N1234567890"
            assert extracted[("i20", "/education/school_name")] == "Example University"
            assert extracted[("i20", "/education/program_name")] == "Computer Science"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_document_uses_declared_document_type_for_evidence_extraction(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-declared-type.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-passport",
                    session_id="sess-1",
                    filename="upload-001.txt",
                    raw_bytes=(
                        b"Full Name: Ada Lovelace\n"
                        b"Passport Number: P1234567\n"
                        b"Nationality: UK"
                    ),
                    artifact_json={"document_type": "passport_bio"},
                )
            )
            db.commit()

        with testing_session_local() as db:
            result = DocumentPipelineService(db).process_document("doc-passport")
            db.commit()

            evidence = db.scalars(
                select(EvidenceItemRecord).order_by(EvidenceItemRecord.field_path)
            ).all()

            assert result["evidence_count"] == 3
            extracted = {(item.evidence_type, item.field_path): item.value for item in evidence}
            assert extracted[("passport_bio", "/identity/full_name")] == "Ada Lovelace"
            assert extracted[("passport_bio", "/identity/passport_number")] == "P1234567"
            assert extracted[("passport_bio", "/identity/nationality")] == "UK"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_document_extracts_non_parent_funding_source(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-funding.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-funding",
                    session_id="sess-1",
                    filename="funding_proof.txt",
                    raw_bytes=b"Employer sponsor bank statement for tuition support",
                )
            )
            db.commit()

        with testing_session_local() as db:
            result = DocumentPipelineService(db).process_document("doc-funding")
            db.commit()

            evidence = db.scalars(select(EvidenceItemRecord)).all()

            assert result["evidence_count"] == 1
            assert len(evidence) == 1
            assert evidence[0].field_path == "/funding/primary_source"
            assert evidence[0].value == "employer"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_document_normalizes_declared_funding_document_alias(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-funding-alias.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-bank",
                    session_id="sess-1",
                    filename="upload-001.txt",
                    raw_bytes=b"Employer sponsor bank statement for tuition support",
                    artifact_json={"document_type": "bank_statement"},
                )
            )
            db.commit()

        with testing_session_local() as db:
            DocumentPipelineService(db).process_document("doc-bank")
            db.commit()

            document = DocumentRepository(db).get_document("doc-bank")
            evidence = db.scalars(select(EvidenceItemRecord)).all()

            assert document is not None
            assert document.artifact_json["metadata"]["document_type"] == "funding_proof"
            assert len(evidence) == 1
            assert evidence[0].evidence_type == "funding_proof"
            assert evidence[0].value == "employer"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_document_preserves_gate_feedback_metadata_from_upload_stage(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-upload-feedback.sqlite3'}",
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
                    filename="passport_bio.txt",
                    raw_bytes=b"Travel flyer",
                    artifact_json={
                        "document_type": "passport_bio",
                        "counts_toward_gate": False,
                        "feedback_message": "这份文件看起来不像当前要求的 passport_bio 材料，请检查后重新上传。",
                        "relevant": False,
                        "main_flow_feedback": {
                            "status": "not_helpful",
                            "supported_document_type": None,
                            "current_focus_document_type": "passport_bio",
                            "message": "这份材料对当前主线没有直接帮助。 当前最缺的关键证明是 passport_bio。",
                        },
                    },
                )
            )
            db.commit()

        with testing_session_local() as db:
            DocumentPipelineService(db).process_document("doc-1")
            db.commit()

            document = DocumentRepository(db).get_document("doc-1")

            assert document is not None
            assert document.artifact_json["metadata"]["document_type"] == "passport_bio"
            assert document.artifact_json["metadata"]["counts_toward_gate"] is False
            assert document.artifact_json["metadata"]["feedback_message"] == (
                "这份文件看起来不像当前要求的 passport_bio 材料，请检查后重新上传。"
            )
            assert document.artifact_json["metadata"]["relevant"] is False
            assert document.artifact_json["metadata"]["main_flow_feedback"] == {
                "status": "not_helpful",
                "supported_document_type": None,
                "current_focus_document_type": "passport_bio",
                "message": "这份材料对当前主线没有直接帮助。 当前最缺的关键证明是 passport_bio。",
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_process_supported_pdf_uses_multimodal_structured_extraction(tmp_path) -> None:
    class StubMultimodalService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def extract(
            self,
            *,
            filename: str,
            raw_bytes: bytes,
            source_type: DocumentSourceType,
            document_type: str | None,
        ) -> MultimodalExtractionResult | None:
            self.calls.append((filename, source_type.value, document_type or ""))
            return MultimodalExtractionResult(
                source_type=source_type,
                parser_name="multimodal_llm",
                full_text=(
                    "Full Name: Ada Lovelace\n"
                    "Passport Number: P1234567\n"
                    "Travel Purpose: Attend academic program"
                ),
                segments=[
                    {
                        "ordinal": 0,
                        "page_number": 1,
                        "text": (
                            "Full Name: Ada Lovelace\n"
                            "Passport Number: P1234567\n"
                            "Travel Purpose: Attend academic program"
                        ),
                    }
                ],
                fields=[
                    MultimodalExtractedField(
                        field_path="/identity/full_name",
                        value="Ada Lovelace",
                        excerpt="Full Name: Ada Lovelace",
                        confidence=0.99,
                        page_number=1,
                    ),
                    MultimodalExtractedField(
                        field_path="/identity/passport_number",
                        value="P1234567",
                        excerpt="Passport Number: P1234567",
                        confidence=0.98,
                        page_number=1,
                    ),
                    MultimodalExtractedField(
                        field_path="/visa_intent/travel_purpose",
                        value="Attend academic program",
                        excerpt="Travel Purpose: Attend academic program",
                        confidence=0.97,
                        page_number=1,
                    ),
                ],
            )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'document-pipeline-multimodal.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-ds160",
                    session_id="sess-1",
                    filename="ds160.pdf",
                    raw_bytes=build_pdf_bytes("Locally extracted text should not win"),
                )
            )
            db.commit()

        stub = StubMultimodalService()
        with testing_session_local() as db:
            result = DocumentPipelineService(
                db,
                multimodal_service=stub,
            ).process_document("doc-ds160")
            db.commit()

            document = DocumentRepository(db).get_document("doc-ds160")
            evidence = db.scalars(
                select(EvidenceItemRecord).order_by(EvidenceItemRecord.field_path)
            ).all()
            chunks = db.scalars(select(DocumentChunkRecord)).all()

            assert result == {"chunk_count": 1, "evidence_count": 3}
            assert stub.calls == [("ds160.pdf", "pdf", "ds160")]
            assert document is not None
            assert document.raw_text.startswith("Full Name: Ada Lovelace")
            assert document.artifact_json["parser_name"] == "multimodal_llm"
            assert document.artifact_json["metadata"]["multimodal_used"] is True
            assert len(chunks) == 1
            assert chunks[0].page_number == 1

            extracted = {(item.evidence_type, item.field_path): item.value for item in evidence}
            assert extracted[("ds160", "/identity/full_name")] == "Ada Lovelace"
            assert extracted[("ds160", "/identity/passport_number")] == "P1234567"
            assert (
                extracted[("ds160", "/visa_intent/travel_purpose")]
                == "Attend academic program"
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
