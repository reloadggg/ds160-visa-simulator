from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    CaseMemorySnapshotRecord,
    DocumentRecord,
    SessionRecord,
    SessionTurnRecord,
)
from app.domain.case_memory import (
    CaseClaim,
    CaseConflict,
    DocumentTypeCandidate,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
    ProofPoint,
)
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.case_board_projection import missing_evidence_from_case_board
from app.services.case_memory_service import (
    CASE_MEMORY_USER_CLAIMS_KEY,
    CaseMemoryService,
)


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

            persisted = db.get(CaseMemorySnapshotRecord, "sess-1")
            assert persisted is not None
            assert persisted.snapshot_json["schema_version"] == (
                "case_memory_snapshot.v1"
            )
            assert persisted.snapshot_json["claims"][0]["claim_id"] == "claim-school"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_snapshot_persist_handles_concurrent_first_insert(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-snapshot-race.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(
                CaseMemorySnapshotRecord(
                    session_id="sess-1",
                    snapshot_json={"schema_version": "old"},
                )
            )
            db.commit()

            service = CaseMemoryService(db)
            snapshot = service.build_snapshot("sess-1")
            original_get = db.get
            snapshot_get_calls = 0

            def stale_first_get(entity, ident, *args, **kwargs):
                nonlocal snapshot_get_calls
                if entity is CaseMemorySnapshotRecord and ident == "sess-1":
                    snapshot_get_calls += 1
                    if snapshot_get_calls == 1:
                        return None
                return original_get(entity, ident, *args, **kwargs)

            monkeypatch.setattr(db, "get", stale_first_get)

            service._persist_snapshot("sess-1", snapshot)
            db.commit()

            persisted = original_get(CaseMemorySnapshotRecord, "sess-1")
            assert persisted is not None
            assert persisted.snapshot_json["schema_version"] == (
                "case_memory_snapshot.v1"
            )
            assert snapshot_get_calls >= 2
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


def test_case_memory_service_queries_evidence_graph_by_field_path(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-evidence-graph.sqlite3'}",
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
                ),
                EvidenceCard(
                    evidence_id="ev-funding",
                    source_type="uploaded_file",
                    document_id="doc-i20",
                    excerpt="Funding source: parents",
                    claim_refs=["claim-funding"],
                    confidence=0.88,
                ),
            ],
            extracted_claims=[
                CaseClaim(
                    claim_id="claim-school",
                    field_path="/education/school_name",
                    value="Example University",
                    status="documented",
                    supporting_evidence_ids=["ev-school"],
                    confidence=0.93,
                ),
                CaseClaim(
                    claim_id="claim-funding",
                    field_path="/funding/primary_source",
                    value="parents",
                    status="documented",
                    supporting_evidence_ids=["ev-funding"],
                    confidence=0.88,
                ),
            ],
            proof_points=[
                ProofPoint(
                    proof_point_id="proof-school-choice",
                    visa_family="f1",
                    question="Why did you choose Example University?",
                    status="supported",
                    why_it_matters="School choice is a core F-1 intent proof point.",
                    claim_refs=["claim-school"],
                    evidence_refs=["ev-school"],
                )
            ],
            confidence=0.9,
        )

        with testing_session_local() as db:
            service = CaseMemoryService(db)
            service.upsert_material_understanding(
                document_id="doc-i20",
                job=MaterialUnderstandingJob(
                    job_id="job-i20",
                    document_id="doc-i20",
                    status="completed",
                    result=result,
                ),
            )
            graph = service.query_evidence_graph(
                "sess-1",
                field_paths=["/education/school_name"],
            )

            assert graph["schema_version"] == "evidence_graph.v1"
            assert [item["claim_id"] for item in graph["claims"]] == [
                "claim-school"
            ]
            assert [item["evidence_id"] for item in graph["evidence_cards"]] == [
                "ev-school"
            ]
            assert [item["proof_point_id"] for item in graph["proof_points"]] == [
                "proof-school-choice"
            ]
            assert graph["conflicts"] == []
            assert graph["edges"] == [
                {
                    "source": "claim-school",
                    "target": "ev-school",
                    "relation": "support",
                },
                {
                    "source": "proof-school-choice",
                    "target": "claim-school",
                    "relation": "requires_claim",
                },
                {
                    "source": "proof-school-choice",
                    "target": "ev-school",
                    "relation": "requires_evidence",
                },
            ]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_public_case_memory_projection_removes_debug_oracle_metadata(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-public-safe.sqlite3'}",
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
                    metadata={
                        "expected_findings": [
                            {"kind": "cross_document_conflict"}
                        ],
                        "synthetic_bundle_id": "dbg-bundle-test",
                        "debug_bundle_scenario": "school_mismatch_bundle",
                        "scenario_label": "学校材料冲突包",
                        "debug_material_bundle": True,
                    },
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
                    metadata={
                        "expected_findings": "oracle hidden from model",
                        "debug_material_bundle": True,
                    },
                )
            ],
            proof_points=[
                ProofPoint(
                    proof_point_id="proof-school",
                    visa_family="f1",
                    question="Does the I-20 document the school?",
                    status="supported",
                    why_it_matters="School context anchors F-1 questioning.",
                    claim_refs=["claim-school"],
                    evidence_refs=["ev-school"],
                    metadata={
                        "synthetic_bundle_id": "dbg-bundle-test",
                        "debug_material_bundle": True,
                    },
                )
            ],
            conflicts=[
                CaseConflict(
                    conflict_id="conflict-school",
                    claim_ids=["claim-school"],
                    evidence_ids=["ev-school"],
                    summary="School statement needs clarification.",
                    severity="medium",
                )
            ],
            confidence=0.93,
        )

        with testing_session_local() as db:
            service = CaseMemoryService(db)
            service.upsert_material_understanding(
                document_id="doc-i20",
                job=MaterialUnderstandingJob(
                    job_id="job-i20",
                    document_id="doc-i20",
                    status="completed",
                    result=result,
                ),
            )

            board = service.public_case_board("sess-1")
            graph = service.public_evidence_graph("sess-1")

            assert board["schema_version"] == "case_board.v1"
            assert graph["schema_version"] == "evidence_graph.v1"
            assert board["claims"][0]["metadata"] == {
                "debug_material_bundle": True
            }
            assert board["evidence_cards"][0]["metadata"] == {
                "debug_material_bundle": True
            }
            serialized = f"{board!r} {graph!r}"
            assert "expected_findings" not in serialized
            assert "cross_document_conflict" not in serialized
            assert "dbg-bundle-test" not in serialized
            assert "school_mismatch_bundle" not in serialized
            assert "学校材料冲突包" not in serialized
            assert "oracle hidden from model" not in serialized
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_board_reads_persisted_snapshot_before_artifact_fallback(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-persisted-read-model.sqlite3'}",
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
            service.upsert_material_understanding(
                document_id="doc-i20",
                job=MaterialUnderstandingJob(
                    job_id="job-i20",
                    document_id="doc-i20",
                    status="completed",
                    result=result,
                ),
            )
            document = DocumentRepository(db).get_document("doc-i20")
            assert document is not None
            document.artifact_json = {"document_type": "i20"}
            db.add(document)
            db.commit()

        with testing_session_local() as db:
            board = CaseMemoryService(db).build_board("sess-1")

            assert [item["claim_id"] for item in board["claims"]] == [
                "claim-school"
            ]
            assert [item["evidence_id"] for item in board["evidence_cards"]] == [
                "ev-school"
            ]
            assert board["latest_material"]["document_id"] == "doc-i20"
            assert board["latest_material"]["understanding_status"] == "completed"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_snapshot_persists_latest_material_status(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-latest-material.sqlite3'}",
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
                    filename="funding.pdf",
                    artifact_json={"document_type": "funding_proof"},
                    raw_bytes=b"funding",
                )
            )
            db.commit()

        job = MaterialUnderstandingJob(
            job_id="job-funding",
            document_id="doc-funding",
            status="failed",
            error_code="parse_failed",
            error_message="PDF text extraction failed before understanding.",
        )

        with testing_session_local() as db:
            service = CaseMemoryService(db)
            snapshot = service.upsert_material_understanding(
                document_id="doc-funding",
                job=job,
            )
            db.commit()

            assert snapshot.latest_material == {
                "document_id": "doc-funding",
                "filename": "funding.pdf",
                "understanding_status": "failed",
                "unknowns": ["PDF text extraction failed before understanding."],
            }

            persisted = db.get(CaseMemorySnapshotRecord, "sess-1")
            assert persisted is not None
            assert persisted.snapshot_json["latest_material"] == snapshot.latest_material

        with testing_session_local() as db:
            document = DocumentRepository(db).get_document("doc-funding")
            assert document is not None
            document.artifact_json = {"document_type": "funding_proof"}
            db.add(document)
            db.commit()

        with testing_session_local() as db:
            board = CaseMemoryService(db).public_case_board("sess-1")

            assert board["latest_material"] == {
                "document_id": "doc-funding",
                "filename": "funding.pdf",
                "understanding_status": "failed",
                "unknowns": ["PDF text extraction failed before understanding."],
            }
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
            assert [
                item.model_dump(mode="json")
                for item in resolved.conflict_resolutions
            ] == [
                {
                    "conflict_id": "conflict-funding-primary-source",
                    "status": "resolved",
                    "note": "applicant clarified sponsor wording",
                }
            ]

            board = service.public_case_board("sess-1")
            assert board["conflict_resolutions"] == [
                {
                    "conflict_id": "conflict-funding-primary-source",
                    "status": "resolved",
                    "note": "applicant clarified sponsor wording",
                }
            ]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_tracks_stated_funding_until_documented_proof_arrives(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-funding-gap.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-funding-gap", declared_family="f1"))
            turn = SessionTurnRepository(db).append_user_turn(
                session_id="sess-funding-gap",
                content=(
                    "My mother and father will cover all my tuition and living "
                    "expenses."
                ),
                source="user_message",
                commit=False,
            )
            db.add(
                DocumentRecord(
                    document_id="doc-bank",
                    session_id="sess-funding-gap",
                    filename="funding.pdf",
                    artifact_json={"document_type": "funding_proof"},
                    raw_bytes=b"bank",
                )
            )
            db.commit()
            turn_id = turn.turn_id

        with testing_session_local() as db:
            service = CaseMemoryService(db)
            claims = service.extract_explicit_user_turn_claims(
                turn_id=turn_id,
                message_text=(
                    "My mother and father will cover all my tuition and living "
                    "expenses."
                ),
            )
            snapshot = service.add_user_turn_claims(
                session_id="sess-funding-gap",
                turn_id=turn_id,
                claims=claims,
            )

            assert [proof.proof_point_id for proof in snapshot.proof_points] == [
                "funding_proof"
            ]
            assert snapshot.proof_points[0].status == "missing"
            board = service.public_case_board("sess-funding-gap")
            assert missing_evidence_from_case_board(board) == ["funding_proof"]

            service.upsert_material_understanding(
                document_id="doc-bank",
                job=MaterialUnderstandingJob(
                    job_id="job-bank",
                    document_id="doc-bank",
                    status="completed",
                    result=MaterialUnderstandingResult(
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
                    ),
                ),
            )
            board_after = service.public_case_board("sess-funding-gap")

            assert missing_evidence_from_case_board(board_after) == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_user_funding_extractor_ignores_project_responsibility(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-user-funding-context.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            service = CaseMemoryService(db)

            assert (
                service.extract_explicit_user_turn_claims(
                    turn_id="turn-project",
                    message_text="我自己主要负责这个 AI 项目的数据整理和模型评估。",
                )
                == []
            )

            claims = service.extract_explicit_user_turn_claims(
                turn_id="turn-funding",
                message_text="我自己支付学费和生活费。",
            )
            assert [(claim.field_path, claim.value) for claim in claims] == [
                ("/funding/primary_source", "self")
            ]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_canonicalizes_parent_funding_aliases(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-funding-alias.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-alias", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-family",
                    session_id="sess-alias",
                    filename="bank.pdf",
                    artifact_json={"document_type": "funding_proof"},
                    raw_bytes=b"bank",
                )
            )
            db.commit()

        result = MaterialUnderstandingResult(
            evidence_cards=[
                EvidenceCard(
                    evidence_id="ev-family",
                    source_type="uploaded_file",
                    document_id="doc-family",
                    excerpt="Primary source of support: Family",
                    claim_refs=["claim-family-funding"],
                    confidence=0.88,
                ),
                EvidenceCard(
                    evidence_id="ev-parent",
                    source_type="uploaded_file",
                    document_id="doc-family",
                    excerpt="Primary source of support: Parents",
                    claim_refs=["claim-parent-funding"],
                    confidence=0.9,
                ),
            ],
            extracted_claims=[
                CaseClaim(
                    claim_id="claim-family-funding",
                    field_path="/funding/primary_source",
                    value="Family",
                    status="documented",
                    supporting_evidence_ids=["ev-family"],
                    confidence=0.88,
                ),
                CaseClaim(
                    claim_id="claim-parent-funding",
                    field_path="/funding/primary_source",
                    value="Parents",
                    status="documented",
                    supporting_evidence_ids=["ev-parent"],
                    confidence=0.9,
                ),
            ],
            confidence=0.9,
        )

        with testing_session_local() as db:
            snapshot = CaseMemoryService(db).upsert_material_understanding(
                document_id="doc-family",
                job=MaterialUnderstandingJob(
                    job_id="job-family",
                    document_id="doc-family",
                    status="completed",
                    result=result,
                ),
            )

            assert {claim.value for claim in snapshot.claims} == {"parents"}
            assert snapshot.conflicts == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_case_memory_rebuild_ignores_stale_user_funding_metadata(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'case-memory-stale-user-funding.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-stale", declared_family="f1"))
            db.add(
                SessionTurnRecord(
                    turn_id="turn-stale",
                    turn_index=1,
                    session_id="sess-stale",
                    role="user",
                    content="我自己主要负责这个 AI 项目的数据整理和模型评估。",
                    source="user_message",
                    metadata_json={
                        CASE_MEMORY_USER_CLAIMS_KEY: [
                            CaseClaim(
                                claim_id="claim-stale-funding",
                                field_path="/funding/primary_source",
                                value="self",
                                status="stated",
                                confidence=0.72,
                                metadata={
                                    "source": "explicit_user_turn",
                                    "capture_method": "conservative_phrase_match",
                                },
                            ).model_dump(mode="json")
                        ]
                    },
                )
            )
            db.commit()

            snapshot = CaseMemoryService(db).build_snapshot("sess-stale")

            assert [
                claim
                for claim in snapshot.claims
                if claim.field_path == "/funding/primary_source"
            ] == []
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

            persisted = db.get(CaseMemorySnapshotRecord, "sess-1")
            assert persisted is not None
            assert persisted.snapshot_json["claims"] == []
            assert persisted.snapshot_json["evidence_cards"] == []
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
