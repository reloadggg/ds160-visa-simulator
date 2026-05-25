from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import DocumentRecord, SessionRecord
from app.domain.case_memory import (
    CaseClaim,
    DocumentTypeCandidate,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
)
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.case_memory_service import CaseMemoryService


def test_case_memory_service_persists_material_understanding_in_document_artifact(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-i20",
                    session_id="sess-1",
                    filename="i20.pdf",
                    artifact_json={"document_type": "i20"},
                    raw_bytes=b"i20",
                )
            )
            db.commit()

        result = MaterialUnderstandingResult(
            document_type_candidates=[
                DocumentTypeCandidate(document_type="i20", confidence=0.91)
            ],
            evidence_cards=[
                EvidenceCard(
                    evidence_id="ev-school",
                    source_type="uploaded_file",
                    document_id="doc-i20",
                    excerpt="School Name: Example University",
                    claim_refs=["claim-school"],
                    confidence=0.93,
                )
            ],
            extracted_claims=[
                CaseClaim(
                    claim_id="claim-school",
                    field_path="/education/school_name",
                    value="Example University",
                    status="documented",
                    supporting_evidence_ids=["ev-school"],
                    confidence=0.93,
                )
            ],
            suggested_followups=[
                InterviewNextMove(
                    move_type="ask",
                    question="Why did you choose Example University?",
                    reason="The I-20 documents the school; the next interview step should verify motivation.",
                    claim_refs=["claim-school"],
                    evidence_refs=["ev-school"],
                )
            ],
            confidence=0.91,
        )
        job = MaterialUnderstandingJob(
            job_id="job-1",
            document_id="doc-i20",
            status="completed",
            result=result,
        )

        with testing_session_local() as db:
            snapshot = CaseMemoryService(db).upsert_material_understanding(
                document_id="doc-i20",
                job=job,
            )
            db.commit()

            document = DocumentRepository(db).get_document("doc-i20")

            assert document is not None
            assert document.artifact_json["understanding_status"] == "completed"
            assert (
                document.artifact_json["material_understanding_result"]["confidence"]
                == 0.91
            )
            assert document.artifact_json["case_board_delta"]["latest_material"] == {
                "document_id": "doc-i20",
                "filename": "i20.pdf",
                "understanding_status": "completed",
                "document_type": "i20",
                "document_type_candidates": [
                    {"document_type": "i20", "confidence": 0.91}
                ],
                "confidence": 0.91,
                "unknowns": [],
            }
            assert document.artifact_json["case_board_delta"]["next_move"] == {
                "move_type": "ask",
                "question": "Why did you choose Example University?",
                "reason": (
                    "The I-20 documents the school; the next interview step should "
                    "verify motivation."
                ),
                "claim_refs": ["claim-school"],
                "evidence_refs": ["ev-school"],
            }
            assert snapshot.claims[0].field_path == "/education/school_name"
            assert snapshot.evidence_cards[0].evidence_id == "ev-school"
            assert snapshot.next_move is not None
            assert snapshot.next_move.question == "Why did you choose Example University?"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_service_records_unavailable_understanding(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-unavailable.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-img",
                    session_id="sess-1",
                    filename="upload.png",
                    artifact_json={"document_type": None},
                    raw_bytes=b"image",
                )
            )
            db.commit()

        job = MaterialUnderstandingJob(
            job_id="job-1",
            document_id="doc-img",
            status="failed",
            error_code="model_unavailable",
            error_message="Material understanding requires a configured multimodal model.",
        )

        with testing_session_local() as db:
            snapshot = CaseMemoryService(db).upsert_material_understanding(
                document_id="doc-img",
                job=job,
            )
            db.commit()
            document = DocumentRepository(db).get_document("doc-img")

            assert document is not None
            assert document.artifact_json["understanding_status"] == "failed"
            assert document.artifact_json["understanding_error"]["code"] == (
                "model_unavailable"
            )
            assert document.artifact_json["case_board_delta"]["evidence_cards"] == []
            assert snapshot.claims == []
            assert snapshot.evidence_cards == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_service_merges_user_claims_and_material_conflicts(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-conflict.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            turn = SessionTurnRepository(db).append_user_turn(
                session_id="sess-1",
                content="I will pay for the program myself.",
                source="user_message",
                commit=False,
            )
            db.add(
                DocumentRecord(
                    document_id="doc-bank",
                    session_id="sess-1",
                    filename="bank.pdf",
                    artifact_json={"document_type": "funding_proof"},
                    raw_bytes=b"bank",
                )
            )
            db.commit()
            turn_id = turn.turn_id

        result = MaterialUnderstandingResult(
            evidence_cards=[
                EvidenceCard(
                    evidence_id="ev-bank",
                    source_type="uploaded_file",
                    document_id="doc-bank",
                    excerpt="Sponsor: parents",
                    claim_refs=["claim-bank-funding"],
                    confidence=0.9,
                )
            ],
            extracted_claims=[
                CaseClaim(
                    claim_id="claim-bank-funding",
                    field_path="/funding/primary_source",
                    value="parents",
                    status="documented",
                    supporting_evidence_ids=["ev-bank"],
                    confidence=0.9,
                )
            ],
            confidence=0.9,
        )
        job = MaterialUnderstandingJob(
            job_id="job-bank",
            document_id="doc-bank",
            status="completed",
            result=result,
        )

        with testing_session_local() as db:
            service = CaseMemoryService(db)
            claims = service.extract_explicit_user_turn_claims(
                turn_id=turn_id,
                message_text="I will pay for the program myself.",
            )
            service.add_user_turn_claims(
                session_id="sess-1",
                turn_id=turn_id,
                claims=claims,
            )
            snapshot = service.upsert_material_understanding(
                document_id="doc-bank",
                job=job,
            )

            statuses = {claim.value: claim.status for claim in snapshot.claims}
            assert statuses == {"parents": "contradicted", "self": "contradicted"}
            assert snapshot.conflicts[0].conflict_id == "conflict-funding-primary-source"
            assert snapshot.conflicts[0].evidence_ids == [
                "ev-bank",
                f"ev-{turn_id}-claim-{turn_id}-funding-primary-source",
            ]

            resolved = service.resolve_conflicts(
                session_id="sess-1",
                conflict_ids=["conflict-funding-primary-source"],
                resolution_note="applicant clarified sponsor wording",
            )
            assert resolved.conflicts == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_service_tombstones_document_evidence(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-tombstone.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-i20",
                    session_id="sess-1",
                    filename="i20.pdf",
                    artifact_json={"document_type": "i20"},
                    raw_bytes=b"i20",
                )
            )
            db.commit()

        result = MaterialUnderstandingResult(
            evidence_cards=[
                EvidenceCard(
                    evidence_id="ev-school",
                    source_type="uploaded_file",
                    document_id="doc-i20",
                    excerpt="School Name: Example University",
                    claim_refs=["claim-school"],
                    confidence=0.93,
                )
            ],
            extracted_claims=[
                CaseClaim(
                    claim_id="claim-school",
                    field_path="/education/school_name",
                    value="Example University",
                    status="documented",
                    supporting_evidence_ids=["ev-school"],
                    confidence=0.93,
                )
            ],
            confidence=0.93,
        )

        with testing_session_local() as db:
            service = CaseMemoryService(db)
            before = service.upsert_material_understanding(
                document_id="doc-i20",
                job=MaterialUnderstandingJob(
                    job_id="job-i20",
                    document_id="doc-i20",
                    status="completed",
                    result=result,
                ),
            )
            assert before.claims

            after = service.tombstone_document(document_id="doc-i20")
            document = DocumentRepository(db).get_document("doc-i20")

            assert after.claims == []
            assert after.evidence_cards == []
            assert document is not None
            assert document.status == "tombstoned"
            assert document.artifact_json["case_memory_tombstone"]["status"] == (
                "tombstoned"
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
