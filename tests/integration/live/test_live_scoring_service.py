import pytest

from app.agents.schemas import ConsistencyFinding
from app.agents.scoring_agent import ScoringAgentRunner
from app.domain.contracts import ApplicantProfile
from app.services.scoring_service import ScoringService


@pytest.mark.live_llm
def test_live_scoring_requests_funding_proof_when_parent_claim_unproven(
    live_db_session_factory,
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-live-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"
    run_calls: list[str] = []
    original_run = ScoringAgentRunner.run

    def tracked_run(self, *, deps, profile_payload, findings):
        run_calls.append(deps.session_id)
        return original_run(
            self,
            deps=deps,
            profile_payload=profile_payload,
            findings=findings,
        )

    monkeypatch.setattr(ScoringAgentRunner, "run", tracked_run)

    with live_db_session_factory() as db:
        score = ScoringService(db=db).propose(
            profile,
            findings=[
                ConsistencyFinding(
                    finding_type="gap",
                    severity="medium",
                    status="supported",
                    summary="funding source claimed but not yet documented",
                    evidence_refs=[],
                )
            ],
            scoring_stage="interview_turn",
        )

    assert "funding_proof" in score.missing_evidence
    assert score.document_readiness <= 40
    assert run_calls
    assert run_calls[-1] == "live-score-1"


@pytest.mark.live_llm
def test_live_scoring_elevates_confirmed_hard_conflict(
    live_db_session_factory,
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-live-score-2")
    profile.visa_intent["declared_family"] = "f1"
    run_calls: list[str] = []
    original_run = ScoringAgentRunner.run

    def tracked_run(self, *, deps, profile_payload, findings):
        run_calls.append(deps.session_id)
        return original_run(
            self,
            deps=deps,
            profile_payload=profile_payload,
            findings=findings,
        )

    monkeypatch.setattr(ScoringAgentRunner, "run", tracked_run)

    with live_db_session_factory() as db:
        score = ScoringService(db=db).propose(
            profile,
            findings=[
                ConsistencyFinding(
                    finding_type="hard_conflict",
                    severity="high",
                    status="confirmed",
                    summary="applicant self-reported false or fraudulent record",
                    evidence_refs=["msg:last_user_turn"],
                )
            ],
            scoring_stage="interview_turn",
        )

    assert any(flag.code == "hard_conflict" for flag in score.risk_flags)
    assert score.narrative_consistency <= 15
    assert score.confidence >= 85
    assert run_calls
    assert run_calls[-1] == "live-score-2"
