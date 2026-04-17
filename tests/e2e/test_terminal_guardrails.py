import json
from pathlib import Path

from app.domain.contracts import ApplicantProfile, ScoreState
from app.services.governor_service import GovernorService


def test_terminal_guardrail_rejects_weak_signal_without_refs() -> None:
    fixture_payload = json.loads(
        Path("fixtures/shared/terminal_weak_signal_blocked.json").read_text(),
    )
    profile = ApplicantProfile.minimal("profile-guardrail")
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")

    decision = GovernorService().decide(
        profile,
        score,
        early_term_candidate=fixture_payload,
    )

    assert decision["decision"] == "need_more_evidence"
