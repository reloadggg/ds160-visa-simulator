class ReportService:
    def user_report(
        self,
        session_id: str,
        visa_family: str,
        governor_decision: str,
        profile_json: dict,
    ) -> dict:
        missing_evidence: list[str] = []
        if profile_json.get("funding", {}).get("primary_source") == "parents":
            missing_evidence.append("funding_proof")

        summary = "当前材料主线可识别，但关键资金支持证据尚不完整。"
        if not missing_evidence:
            summary = "当前材料主线基本完整，可继续常规 interview。"

        return {
            "session_id": session_id,
            "visa_family": visa_family,
            "governor_decision": governor_decision,
            "outcome_label": "需补强关键证据",
            "summary": summary,
            "strengths": ["已完成基本签证家族识别"],
            "risk_points": [],
            "missing_evidence": missing_evidence,
            "recommended_improvements": ["补充资金证明后再继续正式 interview。"],
        }

    def internal_report(
        self,
        session_id: str,
        visa_family: str,
        governor_decision: str,
        profile_json: dict,
    ) -> dict:
        return {
            "session_id": session_id,
            "policy_pack_trace": {"policy_pack_id": f"{visa_family}.default.v1"},
            "runtime_trace": [],
            "score_history": [],
            "governor_history": [{"decision": governor_decision}],
            "profile_snapshot": profile_json,
        }
