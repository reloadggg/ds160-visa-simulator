import re

from app.domain.contracts import ApplicantProfile


class ConsistencyService:
    def evaluate(self, profile: ApplicantProfile) -> list[dict]:
        findings: list[dict] = []
        last_user_message = profile.ds160_view.get("last_user_message", "").lower()

        hard_conflict_patterns = (
            r"\bi lied\b",
            r"\bi (?:used|submitted|provided|uploaded|brought) fake\b",
            r"\bi (?:forged|forge|faked)\b",
        )
        if any(
            re.search(pattern, last_user_message) is not None
            for pattern in hard_conflict_patterns
        ):
            findings.append(
                {
                    "finding_type": "hard_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "summary": "applicant self-reported false or fraudulent record",
                    "evidence_refs": ["msg:last_user_turn"],
                }
            )

        if (
            profile.funding.get("primary_source") == "parents"
            and not profile.field_provenance["/funding/primary_source"].evidence_refs
        ):
            findings.append(
                {
                    "finding_type": "gap",
                    "severity": "medium",
                    "status": "supported",
                    "summary": "funding source claimed but not yet documented",
                    "evidence_refs": [],
                }
            )
        return findings
