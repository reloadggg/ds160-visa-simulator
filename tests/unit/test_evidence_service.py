from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.services.evidence_service import EvidenceService


def test_get_evidence_excerpt_and_extract_document_fields(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'evidence-service.sqlite3'}",
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
                    artifact_json={"source_type": "text"},
                )
            )
            db.add(
                DocumentChunkRecord(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    session_id="sess-1",
                    ordinal=0,
                    page_number=1,
                    text="Parent sponsor bank statement for tuition support",
                    metadata_json={},
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
                    excerpt="Parent sponsor bank statement for tuition support",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with testing_session_local() as db:
            service = EvidenceService(db)

            excerpt = service.get_evidence_excerpt("evi-1")

            assert excerpt is not None
            assert excerpt.evidence_id == "evi-1"
            assert excerpt.document_id == "doc-1"
            assert excerpt.chunk_id == "chunk-1"
            assert excerpt.filename == "funding_proof.txt"
            assert excerpt.source_type == "text"
            assert (
                service.extract_document_fields("doc-1", "funding_proof")
                == {"primary_source": "parents"}
            )
            assert service.extract_document_fields("doc-1", "other_schema") == {}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_extract_document_fields_returns_non_parent_value(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'evidence-service-non-parent.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-2", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-2",
                    session_id="sess-2",
                    filename="funding_proof_other.txt",
                    artifact_json={"source_type": "text"},
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-2",
                    session_id="sess-2",
                    document_id="doc-2",
                    chunk_id="chunk-2",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="self",
                    excerpt="Self-funded bank statement",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with testing_session_local() as db:
            assert EvidenceService(db).extract_document_fields(
                "doc-2",
                "funding_proof",
            ) == {"primary_source": "self"}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
