from types import SimpleNamespace

from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.services.risk_watch_service import RiskWatchService


def _build_score() -> ScoreState:
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")
    score.missing_evidence = ["funding_proof"]
    return score


def test_risk_watch_service_applies_evasive_document_signal() -> None:
    service = RiskWatchService()
    profile = ApplicantProfile.minimal("profile-risk-watch-1")
    profile.ds160_view["risk_watch"] = {
        "evasive_turn_count": 1,
        "missing_key_proof_turn_count": 1,
    }
    score = _build_score()
    record = SessionRecord(
        session_id="sess-risk-watch-1",
        current_focus_json={
            "kind": "required_document",
            "document_type": "funding_proof",
        },
    )

    service.apply_risk_watch_signals(
        record,
        profile,
        score,
        history_turns=[SimpleNamespace(role="user", turn_id="turn-user-2")],
        message_text="I will explain my major first.",
    )

    assert profile.ds160_view["risk_watch"] == {
        "evasive_turn_count": 2,
        "missing_key_proof_turn_count": 2,
    }
    assert {item.code for item in score.risk_flags} == {
        "evasive_answer",
        "unresolved_key_proof_gap",
    }
    assert score.risk_flags[0].evidence_refs == ["msg:turn-user-2"]


def test_risk_watch_service_high_risk_review_signal_requires_counter_threshold() -> None:
    service = RiskWatchService()
    profile = ApplicantProfile.minimal("profile-risk-watch-2")
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")
    score.risk_flags = [
        RiskFlag(
            code="evasive_answer",
            severity="high",
            status="supported",
            evidence_refs=["msg:turn-user-2"],
        )
    ]

    profile.ds160_view["risk_watch"] = {"evasive_turn_count": 1}
    assert service.high_risk_review_signal(profile, score) is None

    profile.ds160_view["risk_watch"] = {"evasive_turn_count": 2}
    signal = service.high_risk_review_signal(profile, score)

    assert signal is not None
    assert signal.code == "evasive_answer"
