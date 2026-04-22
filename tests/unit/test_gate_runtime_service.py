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


def test_refresh_session_ignores_uploaded_document_marked_outside_gate_flow(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-ignore-uploaded.sqlite3'}",
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
                        scenario_key="ignore-uploaded",
                        required_documents=["funding_proof"],
                    ),
                )
            )
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="sess-1",
                    filename="funding_proof.txt",
                    status="uploaded",
                    artifact_json={
                        "status": "uploaded",
                        "filename": "funding_proof.txt",
                        "document_type": "funding_proof",
                        "counts_toward_gate": False,
                    },
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
            assert record.gate_status_json["status"] == "pending_documents"
            funding_doc = next(
                item
                for item in record.gate_status_json["required_documents"]
                if item["document_type"] == "funding_proof"
            )
            assert funding_doc == {
                "document_type": "funding_proof",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_build_gate_support_reports_primary_missing_document_without_blocking(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-support-pending.sqlite3'}",
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
                        required_documents=["ds160", "passport_bio", "funding_proof"],
                    ),
                )
            )
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="sess-1",
                    filename="passport_bio.txt",
                    status="uploaded",
                    artifact_json={"status": "uploaded", "filename": "passport_bio.txt"},
                )
            )
            db.commit()

        with testing_session_local() as db:
            service = GateRuntimeService(db)
            record = service.refresh_session("sess-1")
            support = service.build_gate_support(record)

            assert support == {
                "requested_documents": ["ds160"],
                "primary_document": "ds160",
                "support_message": "当前最缺的关键证明是 ds160。",
                "gate_progress": {
                    "overall_status": "waiting_for_parse",
                    "ready_count": 0,
                    "uploaded_count": 1,
                    "missing_count": 2,
                    "documents": [
                        {
                            "document_type": "ds160",
                            "status": "missing",
                            "is_uploaded": False,
                            "is_parsed": False,
                            "meets_minimum_fields": False,
                        },
                        {
                            "document_type": "passport_bio",
                            "status": "uploaded",
                            "is_uploaded": True,
                            "is_parsed": False,
                            "meets_minimum_fields": False,
                        },
                        {
                            "document_type": "funding_proof",
                            "status": "missing",
                            "is_uploaded": False,
                            "is_parsed": False,
                            "meets_minimum_fields": False,
                        },
                    ],
                },
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_merge_interview_response_keeps_single_focus_from_interview_output(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-merge.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-merge",
                    declared_family="f1",
                    gate_status_json=build_initial_gate_status(
                        declared_family="f1",
                        scenario_key="parent_sponsored",
                        required_documents=["ds160", "passport_bio", "funding_proof"],
                    ),
                )
            )
            db.commit()

        with testing_session_local() as db:
            service = GateRuntimeService(db)
            record = service.refresh_session("sess-merge")

            merged = service.merge_interview_response(
                {
                    "assistant_message": "Why do you want to study in the U.S.?",
                    "governor_decision": "continue_interview",
                    "score_summary": {
                        "category_fit": 65,
                        "document_readiness": 20,
                        "narrative_consistency": 60,
                        "confidence": 55,
                    },
                    "requested_documents": [],
                },
                record,
            )

            assert merged["assistant_message"] == "Why do you want to study in the U.S.?"
            assert merged["requested_documents"] == []
            assert merged["gate_progress"]["missing_count"] == 3
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


def test_refresh_session_ignores_parsed_document_marked_outside_gate_flow_in_metadata(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate-runtime-ignore-parsed.sqlite3'}",
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
                        scenario_key="ignore-parsed",
                        required_documents=["passport_bio"],
                    ),
                )
            )
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="sess-1",
                    filename="passport_bio.pdf",
                    status="parsed",
                    artifact_json={
                        "status": "parsed",
                        "filename": "passport_bio.pdf",
                        "metadata": {
                            "document_type": "passport_bio",
                            "counts_toward_gate": False,
                        },
                    },
                )
            )
            db.commit()

        with testing_session_local() as db:
            record = GateRuntimeService(db).refresh_session("sess-1")

            assert record.phase_state == "gate_review"
            assert record.gate_status_json["status"] == "pending_documents"
            passport_doc = next(
                item
                for item in record.gate_status_json["required_documents"]
                if item["document_type"] == "passport_bio"
            )
            assert passport_doc == {
                "document_type": "passport_bio",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            }
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

            document.artifact_json = {
                "status": "parsed",
                "metadata": {
                    "document_assessment": {"document_type": "funding_proof"},
                },
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
