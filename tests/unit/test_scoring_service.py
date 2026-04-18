from pydantic_ai.models.test import TestModel

from app.agents.schemas import AgentRuntimeDeps
from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.services.scoring_service import ScoringService


def test_scoring_service_uses_agent_output_when_model_is_available(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.DOCUMENTED,
    )

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "category_fit": 78,
                    "document_readiness": 90,
                    "narrative_consistency": 82,
                    "confidence": 75,
                    "risk_flags": [],
                    "missing_evidence": [],
                    "requested_documents": [],
                }
            ),
            {"model": "test"},
        ),
    )

    monkeypatch.setattr(
        ScoringService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )

    score = ScoringService(db=object()).propose(
        profile,
        findings=[],
        scoring_stage="interview_turn",
    )

    assert score.category_fit == 78
    assert score.document_readiness == 90
    assert score.narrative_consistency == 82
    assert score.confidence == 75
    assert score.missing_evidence == []


def test_scoring_service_maps_proposal_fields_to_score_state(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-score-2")
    profile.visa_intent["declared_family"] = "f1"

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "category_fit": 66,
                    "document_readiness": 55,
                    "narrative_consistency": 44,
                    "confidence": 71,
                    "risk_flags": [
                        {
                            "code": "supporting_evidence_missing",
                            "severity": "medium",
                            "status": "supported",
                            "summary": "funding proof still missing",
                            "evidence_refs": [],
                        }
                    ],
                    "missing_evidence": ["travel_history"],
                    "requested_documents": ["funding_proof"],
                }
            ),
            {"model": "test"},
        ),
    )

    monkeypatch.setattr(
        ScoringService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )

    score = ScoringService(db=object()).propose(
        profile,
        findings=[],
        scoring_stage="interview_turn",
    )

    assert score.category_fit == 66
    assert score.document_readiness == 55
    assert score.narrative_consistency == 44
    assert score.confidence == 71
    assert [flag.code for flag in score.risk_flags] == ["supporting_evidence_missing"]
    assert score.risk_flags[0].severity == "medium"
    assert score.risk_flags[0].status == "supported"
    assert score.missing_evidence == ["travel_history", "funding_proof"]


def test_scoring_service_falls_back_without_model(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-score-3")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (None, {"model": "test"}),
    )

    score = ScoringService().propose(
        profile,
        findings=[
            {
                "finding_type": "gap",
                "severity": "medium",
                "status": "supported",
                "summary": "funding source claimed but not yet documented",
                "evidence_refs": [],
            }
        ],
        scoring_stage="interview_turn",
    )

    assert score.document_readiness == 40
    assert score.narrative_consistency == 55
    assert "funding_proof" in score.missing_evidence


def test_scoring_service_falls_back_when_agent_runtime_errors(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-score-4")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (object(), {"model": "test"}),
    )
    monkeypatch.setattr(
        ScoringService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )
    monkeypatch.setattr(
        "app.services.scoring_service.ScoringAgentRunner.run",
        lambda self, *, deps, profile_payload, findings: (_ for _ in ()).throw(
            RuntimeError("tool failure")
        ),
    )

    score = ScoringService(db=object()).propose(
        profile,
        findings=[
            {
                "finding_type": "gap",
                "severity": "medium",
                "status": "supported",
                "summary": "funding source claimed but not yet documented",
                "evidence_refs": [],
            }
        ],
        scoring_stage="interview_turn",
    )

    assert score.document_readiness == 40
    assert score.narrative_consistency == 55
    assert "funding_proof" in score.missing_evidence


def test_scoring_service_falls_back_when_agent_returns_invalid_proposal(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-score-5")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "category_fit": 85,
                    "document_readiness": 88,
                    "narrative_consistency": 90,
                    "confidence": 92,
                    "risk_flags": [
                        {
                            "code": "hard_conflict",
                            "severity": "high",
                            "status": "confirmed",
                            "summary": "missing refs should invalidate proposal",
                            "evidence_refs": [],
                        }
                    ],
                    "missing_evidence": [],
                    "requested_documents": [],
                },
            ),
            {"model": "test"},
        ),
    )
    monkeypatch.setattr(
        ScoringService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )

    score = ScoringService(db=object()).propose(
        profile,
        findings=[
            {
                "finding_type": "gap",
                "severity": "medium",
                "status": "supported",
                "summary": "funding source claimed but not yet documented",
                "evidence_refs": [],
            }
        ],
        scoring_stage="interview_turn",
    )

    assert score.document_readiness == 40
    assert score.narrative_consistency == 55
    assert "funding_proof" in score.missing_evidence
