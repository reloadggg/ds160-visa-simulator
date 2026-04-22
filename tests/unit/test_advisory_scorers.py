from app.evals.advisory_scorers import (
    build_score_eval_series,
    build_score_eval_summary,
)


def test_build_score_eval_summary_identifies_review_and_refusal_candidates() -> None:
    summary = build_score_eval_summary(
        {
            "scoring_stage": "interview_turn",
            "category_fit": 62,
            "document_readiness": 78,
            "narrative_consistency": 22,
            "confidence": 88,
            "missing_evidence": [],
            "risk_flags": [
                {
                    "code": "hard_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "evidence_refs": ["evi-1"],
                },
                {
                    "code": "overstay_history",
                    "severity": "high",
                    "status": "supported",
                    "evidence_refs": [],
                },
            ],
            "summary": "missing=0 risk_flags=2",
        }
    )

    assert summary.risk_level == "high"
    assert summary.review_candidate_codes == ["hard_conflict", "overstay_history"]
    assert summary.confirmed_high_risk_codes == ["hard_conflict"]
    assert summary.refusal_candidate_codes == ["hard_conflict"]
    assert summary.document_ready is True
    assert summary.needs_more_evidence is False


def test_build_score_eval_summary_tracks_missing_evidence_pressure() -> None:
    summary = build_score_eval_summary(
        {
            "scoring_stage": "interview_turn",
            "category_fit": 60,
            "document_readiness": 40,
            "narrative_consistency": 55,
            "confidence": 65,
            "missing_evidence": ["funding_proof"],
            "risk_flags": [
                {
                    "code": "supporting_evidence_missing",
                    "severity": "medium",
                    "status": "supported",
                    "evidence_refs": [],
                }
            ],
            "summary": "missing=1 risk_flags=1",
        }
    )

    assert summary.risk_level == "medium"
    assert summary.missing_evidence == ["funding_proof"]
    assert summary.missing_evidence_count == 1
    assert summary.document_ready is False
    assert summary.needs_more_evidence is True


def test_build_score_eval_series_preserves_order() -> None:
    series = build_score_eval_series(
        [
            {
                "scoring_stage": "gate_review",
                "category_fit": 0,
                "document_readiness": 20,
                "narrative_consistency": 0,
                "confidence": 0,
                "missing_evidence": ["passport_bio"],
                "risk_flags": [],
                "summary": "missing=1 risk_flags=0",
            },
            {
                "scoring_stage": "interview_turn",
                "category_fit": 70,
                "document_readiness": 78,
                "narrative_consistency": 82,
                "confidence": 76,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            },
        ]
    )

    assert [item["scoring_stage"] for item in series] == [
        "gate_review",
        "interview_turn",
    ]
    assert series[0]["needs_more_evidence"] is True
    assert series[1]["document_ready"] is True
