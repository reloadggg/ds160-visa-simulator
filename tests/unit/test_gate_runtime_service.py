from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.domain.runtime import build_initial_gate_status
from app.services.gate_runtime_service import GateRuntimeService


def test_refresh_session_keeps_family_not_selected_shape(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-family.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family=None,
                    gate_status_json=build_initial_gate_status(None, []),
                )
            )
            db.commit()

        with testing_session_local() as db:
            record = GateRuntimeService(db).refresh_session("sess-1")

            assert record.phase_state == "intake"
            assert record.gate_status_json == {
                "declared_family": None,
                "scenario_key": None,
                "status": "family_not_selected",
                "required_documents": [],
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_refresh_session_marks_uploaded_funding_proof_waiting_for_parse(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-waiting.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family="f1",
                    gate_status_json=build_initial_gate_status(
                        declared_family="f1",
                        scenario_key="parent_sponsored",
                        required_documents=[
                            "ds160",
                            "passport_bio",
                            "i20",
                            "admission_letter",
                            "funding_proof",
                        ],
                    ),
                )
            )
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="sess-1",
                    filename="funding_proof.txt",
                    status="uploaded",
                    artifact_json={"status": "uploaded", "filename": "funding_proof.txt"},
                )
            )
            db.add(
                JobRecord(
                    job_id="job-1",
                    session_id="sess-1",
                    kind="gate_parse",
                    status="queued",
                    payload_json={"document_id": "doc-1"},
                )
            )
            db.commit()

        with testing_session_local() as db:
            record = GateRuntimeService(db).refresh_session("sess-1")

            assert record.phase_state == "gate_review"
            assert record.gate_status_json["status"] == "waiting_for_parse"
            funding_doc = next(
                item
                for item in record.gate_status_json["required_documents"]
                if item["document_type"] == "funding_proof"
            )
            assert funding_doc == {
                "document_type": "funding_proof",
                "status": "uploaded",
                "is_uploaded": True,
                "is_parsed": False,
                "meets_minimum_fields": False,
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_refresh_session_keeps_pending_when_only_funding_proof_is_ready(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-ready.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    profile = ApplicantProfile.minimal("profile-sess-1")
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.DOCUMENTED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
        evidence_refs=["evi-1"],
        source_summary="document evidence",
    )

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                    gate_status_json=build_initial_gate_status(
                        declared_family="f1",
                        scenario_key="parent_sponsored",
                        required_documents=[
                            "ds160",
                            "passport_bio",
                            "i20",
                            "admission_letter",
                            "funding_proof",
                        ],
                    ),
                )
            )
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="sess-1",
                    filename="funding_proof.txt",
                    status="parsed",
                    artifact_json={
                        "status": "parsed",
                        "filename": "funding_proof.txt",
                        "source_type": "text",
                    },
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
            record = GateRuntimeService(db).refresh_session("sess-1")

            assert record.phase_state == "gate_review"
            assert record.gate_status_json["status"] == "pending_documents"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_refresh_session_marks_all_required_documents_ready_after_parse(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-ready.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    profile = ApplicantProfile.minimal("profile-sess-1")
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.DOCUMENTED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
        evidence_refs=["evi-1"],
        source_summary="document evidence",
    )

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                    gate_status_json=build_initial_gate_status(
                        declared_family="f1",
                        scenario_key="parent_sponsored",
                        required_documents=[
                            "ds160",
                            "passport_bio",
                            "i20",
                            "admission_letter",
                            "funding_proof",
                        ],
                    ),
                )
            )
            for document_id, filename in [
                ("doc-1", "ds160.txt"),
                ("doc-2", "passport_bio.txt"),
                ("doc-3", "i20.txt"),
                ("doc-4", "admission_letter.txt"),
                ("doc-5", "funding_proof.txt"),
            ]:
                db.add(
                    DocumentRecord(
                        document_id=document_id,
                        session_id="sess-1",
                        filename=filename,
                        status="parsed",
                        artifact_json={
                            "status": "parsed",
                            "filename": filename,
                            "source_type": "text",
                        },
                    )
                )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id="sess-1",
                    document_id="doc-5",
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
            record = GateRuntimeService(db).refresh_session("sess-1")

            assert record.phase_state == "interview"
            assert record.gate_status_json["status"] == "ready_for_interview"
            assert all(
                item["meets_minimum_fields"]
                for item in record.gate_status_json["required_documents"]
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_matches_document_type_prefers_uploaded_document_type_metadata(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-document-type.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            service = GateRuntimeService(db)
            document = DocumentRecord(
                document_id="doc-1",
                session_id="sess-1",
                filename="bank-statement-final.pdf",
                status="uploaded",
                artifact_json={
                    "status": "uploaded",
                    "filename": "bank-statement-final.pdf",
                    "document_type": "funding_proof",
                },
            )

            assert service._matches_document_type(document, "funding_proof") is True
            assert service._matches_document_type(document, "passport_bio") is False

            document.artifact_json = {
                "status": "parsed",
                "metadata": {"document_type": "funding_proof"},
            }

            assert service._matches_document_type(document, "funding_proof") is True
            assert service._matches_document_type(document, "passport_bio") is False

            document.filename = "funding_proof-final.pdf"
            document.artifact_json = {
                "status": "uploaded",
                "document_type": "passport_bio",
            }

            assert service._matches_document_type(document, "funding_proof") is False
            assert service._matches_document_type(document, "passport_bio") is True
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
