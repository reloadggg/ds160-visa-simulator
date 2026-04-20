from pydantic_ai.models.test import TestModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.services.consistency_service import ConsistencyService
from app.services.scoring_service import ScoringService


def test_tool_based_scoring_keeps_funding_gap_when_parent_claim_unproven(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-tool-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.CLAIMED,
    )
    findings = ConsistencyService().evaluate(profile)

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (None, {"model": "test"}),
    )

    score = ScoringService().propose(
        profile,
        findings=findings,
        scoring_stage="interview_turn",
    )

    assert "funding_proof" in score.missing_evidence


def test_tool_based_scoring_does_not_misclassify_documented_parent_funding(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tool-based-scoring.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="tool-score-2", declared_family="f1"))
            db.add(
                DocumentRecord(
                    document_id="doc-1",
                    session_id="tool-score-2",
                    filename="funding_proof.txt",
                    artifact_json={"source_type": "text"},
                )
            )
            db.add(
                DocumentChunkRecord(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    session_id="tool-score-2",
                    ordinal=0,
                    page_number=1,
                    text="Parent sponsor bank statement for tuition support",
                    metadata_json={},
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id="tool-score-2",
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

        profile = ApplicantProfile.minimal("profile-tool-score-2")
        profile.visa_intent["declared_family"] = "f1"
        profile.funding["primary_source"] = "parents"
        profile.field_states["/funding/primary_source"] = FieldStateRecord(
            state=FieldState.CLAIMED,
        )
        findings = ConsistencyService().evaluate(profile)

        tool_calls: list[tuple[str, str]] = []
        original_search = ScoringService._build_agent_deps

        def spy_build_agent_deps(self, profile):
            deps = original_search(self, profile)
            original_method = deps.retrieval.search_session_evidence

            def tracked_search(
                session_id: str,
                query: str,
                *,
                evidence_type: str | None = None,
                field_path: str | None = None,
                limit: int = 5,
            ):
                tool_calls.append((session_id, query))
                return original_method(
                    session_id,
                    query,
                    evidence_type=evidence_type,
                    field_path=field_path,
                    limit=limit,
                )

            deps.retrieval.search_session_evidence = tracked_search
            return deps

        monkeypatch.setattr(ScoringService, "_build_agent_deps", spy_build_agent_deps)
        monkeypatch.setattr(
            ScoringService,
            "_fallback_score",
            lambda self, profile, findings, scoring_stage: (_ for _ in ()).throw(
                AssertionError("agent path should not fall back")
            ),
        )
        monkeypatch.setattr(
            "app.services.scoring_service.AgentModelFactory.build",
            lambda self, module_key, stage_key: (
                TestModel(
                    call_tools=["search_evidence"],
                    custom_output_args={
                        "category_fit": 75,
                        "document_readiness": 88,
                        "narrative_consistency": 80,
                        "confidence": 74,
                        "risk_flags": [],
                        "missing_evidence": [],
                        "requested_documents": [],
                    }
                ),
                {"model": "test"},
            ),
        )

        with testing_session_local() as db:
            score = ScoringService(db=db).propose(
                profile,
                findings=findings,
                scoring_stage="interview_turn",
            )

        assert score.document_readiness == 88
        assert "funding_proof" not in score.missing_evidence
        assert tool_calls
        assert tool_calls[0][0] == "tool-score-2"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_tool_based_scoring_does_not_turn_requested_documents_into_missing_evidence(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-tool-score-3")
    profile.visa_intent["declared_family"] = "f1"
    findings = ConsistencyService().evaluate(profile)

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "category_fit": 72,
                    "document_readiness": 64,
                    "narrative_consistency": 68,
                    "confidence": 70,
                    "risk_flags": [],
                    "missing_evidence": [],
                    "requested_documents": ["funding_proof"],
                },
            ),
            {"model": "test"},
        ),
    )

    score = ScoringService(db=object()).propose(
        profile,
        findings=findings,
        scoring_stage="interview_turn",
    )

    assert score.missing_evidence == []
