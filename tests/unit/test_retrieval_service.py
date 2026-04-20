from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.services.retrieval_service import RetrievalService


def test_search_session_evidence_returns_ranked_hits(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retrieval-service.sqlite3'}",
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
                        document_id="doc-1",
                        session_id="sess-1",
                        filename="funding_proof.txt",
                        artifact_json={"source_type": "text"},
                    ),
                    DocumentRecord(
                        document_id="doc-2",
                        session_id="sess-1",
                        filename="notes.txt",
                        artifact_json={"source_type": "text"},
                    ),
                ]
            )
            db.add_all(
                [
                    DocumentChunkRecord(
                        chunk_id="chunk-1",
                        document_id="doc-1",
                        session_id="sess-1",
                        ordinal=0,
                        page_number=1,
                        text="Parent sponsor bank statement for tuition support",
                        metadata_json={},
                    ),
                    DocumentChunkRecord(
                        chunk_id="chunk-2",
                        document_id="doc-2",
                        session_id="sess-1",
                        ordinal=0,
                        page_number=1,
                        text="Sponsor letter mentioning tuition only",
                        metadata_json={},
                    ),
                ]
            )
            db.add_all(
                [
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
                    ),
                    EvidenceItemRecord(
                        evidence_id="evi-2",
                        session_id="sess-1",
                        document_id="doc-2",
                        chunk_id="chunk-2",
                        evidence_type="funding_proof",
                        field_path="/funding/primary_source",
                        value="parents",
                        excerpt="Sponsor letter mentioning tuition only",
                        confidence=1.0,
                        metadata_json={},
                    ),
                ]
            )
            db.commit()

        with testing_session_local() as db:
            service = RetrievalService(db)

            hits = service.search_session_evidence(
                "sess-1",
                "parent sponsor tuition",
                evidence_type="funding_proof",
                field_path="/funding/primary_source",
                limit=5,
            )

            assert [hit.evidence_id for hit in hits] == ["evi-1", "evi-2"]
            assert hits[0].document_id == "doc-1"
            assert hits[0].chunk_id == "chunk-1"
            assert hits[0].filename == "funding_proof.txt"
            assert hits[0].source_type == "text"
            assert hits[0].score > hits[1].score
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_search_session_evidence_matches_chinese_tokens(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retrieval-service-zh.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-zh", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-zh",
                    session_id="sess-zh",
                    filename="资助证明.txt",
                    artifact_json={"source_type": "text"},
                )
            )
            db.add(
                DocumentChunkRecord(
                    chunk_id="chunk-zh",
                    document_id="doc-zh",
                    session_id="sess-zh",
                    ordinal=0,
                    page_number=1,
                    text="父母资助银行存款证明",
                    metadata_json={},
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-zh",
                    session_id="sess-zh",
                    document_id="doc-zh",
                    chunk_id="chunk-zh",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="parents",
                    excerpt="父母资助银行存款证明",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with testing_session_local() as db:
            hits = RetrievalService(db).search_session_evidence(
                "sess-zh",
                "父母资助",
                evidence_type="funding_proof",
            )

            assert [hit.evidence_id for hit in hits] == ["evi-zh"]
            assert hits[0].score > 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_search_session_evidence_matches_field_name_queries(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retrieval-service-field-name.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-field", declared_family="f1"))
            db.add_all(
                [
                    DocumentRecord(
                        document_id="doc-passport",
                        session_id="sess-field",
                        filename="upload-001.pdf",
                        artifact_json={"source_type": "pdf"},
                    ),
                    DocumentRecord(
                        document_id="doc-name",
                        session_id="sess-field",
                        filename="upload-002.pdf",
                        artifact_json={"source_type": "pdf"},
                    ),
                ]
            )
            db.add_all(
                [
                    DocumentChunkRecord(
                        chunk_id="chunk-passport",
                        document_id="doc-passport",
                        session_id="sess-field",
                        ordinal=0,
                        page_number=1,
                        text="P1234567",
                        metadata_json={},
                    ),
                    DocumentChunkRecord(
                        chunk_id="chunk-name",
                        document_id="doc-name",
                        session_id="sess-field",
                        ordinal=0,
                        page_number=1,
                        text="Ada Lovelace",
                        metadata_json={},
                    ),
                ]
            )
            db.add_all(
                [
                    EvidenceItemRecord(
                        evidence_id="evi-passport",
                        session_id="sess-field",
                        document_id="doc-passport",
                        chunk_id="chunk-passport",
                        evidence_type="passport_bio",
                        field_path="/identity/passport_number",
                        value="P1234567",
                        excerpt="P1234567",
                        confidence=1.0,
                        metadata_json={},
                    ),
                    EvidenceItemRecord(
                        evidence_id="evi-name",
                        session_id="sess-field",
                        document_id="doc-name",
                        chunk_id="chunk-name",
                        evidence_type="passport_bio",
                        field_path="/identity/full_name",
                        value="Ada Lovelace",
                        excerpt="Ada Lovelace",
                        confidence=1.0,
                        metadata_json={},
                    ),
                ]
            )
            db.commit()

        with testing_session_local() as db:
            hits = RetrievalService(db).search_session_evidence(
                "sess-field",
                "passport number",
                limit=5,
            )

            assert hits[0].evidence_id == "evi-passport"
            assert hits[0].field_path == "/identity/passport_number"
            assert hits[0].score > hits[1].score
            assert hits[0].score > 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
