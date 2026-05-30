from app.services.interview_review_service import InterviewReviewService


def test_fallback_review_copy_avoids_weak_proof_checklist_framing() -> None:
    service = InterviewReviewService.__new__(InterviewReviewService)

    report = service._fallback_report(
        {
            "session": {"phase_state": "interview"},
            "user_report": {
                "summary": "当前仍有事实需要核实。",
                "missing_evidence": [],
                "risk_points": [],
                "recommended_improvements": [],
                "strengths": [],
            },
            "documents": [],
        }
    )

    copy = " ".join(
        [
            report.executive_summary,
            *report.missing_or_weak_evidence,
            *report.improvement_plan,
            *report.next_practice_focus,
        ]
    )
    assert "薄弱证明点" not in copy
    assert "关键证明" not in copy
    assert "请准备以下材料" not in copy
