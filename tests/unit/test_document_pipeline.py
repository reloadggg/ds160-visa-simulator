from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.repositories.document_repo import DocumentRepository
from app.services.document_pipeline import DocumentPipelineService


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
