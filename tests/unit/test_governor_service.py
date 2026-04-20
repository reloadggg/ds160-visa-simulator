from app.domain.contracts import ApplicantProfile, GovernorDecision, RiskFlag, ScoreState
from app.services.governor_service import GovernorService


def test_governor_blocks_refusal_when_only_low_score_exists() -> None:
    profile = ApplicantProfile.minimal("profile-1")
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")

    decision = GovernorService().decide(profile, score, early_term_candidate=None)

    assert decision["decision"] == GovernorDecision.CONTINUE_INTERVIEW.value
    assert "low_score_only_blocked" in decision["blocked_actions"]


def test_governor_blocks_refusal_without_evidence_refs() -> None:
    profile = ApplicantProfile.minimal("profile-2")
    score = ScoreState(
        score_state_id="score-2",
        profile_version=2,
        scoring_stage="interview_turn",
        category_fit=20,
        document_readiness=30,
        narrative_consistency=25,
        confidence=82,
        risk_flags=[],
    )

    decision = GovernorService().decide(
        profile,
        score,
        early_term_candidate={
            "eligible": True,
            "policy_id": "f1.tp.hard_conflict",
            "reason_code": "hard_conflict",
            "confirmation_required": False,
            "evidence_refs": [],
        },
    )

    assert decision["decision"] == GovernorDecision.CONTINUE_INTERVIEW.value
    assert "missing_evidence_refs_blocked" in decision["blocked_actions"]


def test_governor_allows_refusal_when_terminal_candidate_has_refs() -> None:
    profile = ApplicantProfile.minimal("profile-3")
    score = ScoreState(
        score_state_id="score-3",
        profile_version=2,
        scoring_stage="interview_turn",
        category_fit=20,
        document_readiness=30,
        narrative_consistency=25,
        confidence=82,
        risk_flags=[
            RiskFlag(
                code="hard_conflict",
                severity="high",
                status="confirmed",
                evidence_refs=["ev-1"],
            )
        ],
    )

    decision = GovernorService().decide(
        profile,
        score,
        early_term_candidate={
            "eligible": True,
            "policy_id": "f1.tp.confirmed_hard_conflict",
            "reason_code": "hard_conflict",
            "confirmation_required": False,
            "evidence_refs": ["ev-1"],
        },
    )

    assert decision["decision"] == GovernorDecision.SIMULATED_REFUSAL.value


def test_governor_keeps_confirmed_non_redline_conflict_out_of_direct_refusal() -> None:
    profile = ApplicantProfile.minimal("profile-4")
    score = ScoreState(
        score_state_id="score-4",
        profile_version=2,
        scoring_stage="interview_turn",
        category_fit=40,
        document_readiness=35,
        narrative_consistency=30,
        confidence=82,
        risk_flags=[
            RiskFlag(
                code="record_conflict",
                severity="high",
                status="confirmed",
                evidence_refs=["ev-2"],
            )
        ],
    )

    decision = GovernorService().decide(
        profile,
        score,
        early_term_candidate={
            "eligible": True,
            "policy_id": "f1.tp.record_conflict",
            "reason_code": "record_conflict",
            "confirmation_required": False,
            "evidence_refs": ["ev-2"],
        },
    )

    assert decision["decision"] == GovernorDecision.CONTINUE_INTERVIEW.value
    assert "non_redline_terminal_blocked" in decision["blocked_actions"]


def test_governor_allows_refusal_when_redline_code_exists_alongside_other_high_risks() -> None:
    profile = ApplicantProfile.minimal("profile-5")
    score = ScoreState(
        score_state_id="score-5",
        profile_version=2,
        scoring_stage="interview_turn",
        category_fit=35,
        document_readiness=40,
        narrative_consistency=15,
        confidence=90,
        risk_flags=[
            RiskFlag(
                code="record_conflict",
                severity="high",
                status="confirmed",
                evidence_refs=["ev-record"],
            ),
            RiskFlag(
                code="hard_conflict",
                severity="high",
                status="confirmed",
                evidence_refs=["ev-hard"],
            ),
        ],
    )

    decision = GovernorService().decide(
        profile,
        score,
        early_term_candidate={
            "eligible": True,
            "policy_id": "f1.tp.hard_conflict",
            "reason_code": "hard_conflict",
            "confirmation_required": False,
            "evidence_refs": ["ev-hard"],
        },
    )

    assert decision["decision"] == GovernorDecision.SIMULATED_REFUSAL.value
    assert decision["rationale_refs"] == ["ev-hard"]
