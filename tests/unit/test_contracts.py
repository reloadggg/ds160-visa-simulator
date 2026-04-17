from app.domain.contracts import (
    ApplicantProfile,
    FieldState,
    GovernorDecision,
    ScoreState,
)


def test_applicant_profile_tracks_unknown_field_state() -> None:
    profile = ApplicantProfile.minimal(profile_id="profile-1")

    assert profile.field_states["/funding/primary_source"].state == FieldState.UNKNOWN


def test_score_state_allows_missing_evidence_without_false_fact() -> None:
    score = ScoreState.minimal(profile_version=1, scoring_stage="gate_review")

    assert score.missing_evidence == []
    assert score.category_fit == 0


def test_governor_decision_enum_contains_supported_values() -> None:
    assert GovernorDecision.SIMULATED_REFUSAL.value == "simulated_refusal"
