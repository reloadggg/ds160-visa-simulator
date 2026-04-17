from app.domain.contracts import ApplicantProfile


class ConsistencyService:
    def evaluate(self, profile: ApplicantProfile) -> list[dict]:
        findings: list[dict] = []
        if (
            profile.funding.get("primary_source") == "parents"
            and not profile.field_provenance["/funding/primary_source"].evidence_refs
        ):
            findings.append(
                {
                    "finding_type": "gap",
                    "severity": "medium",
                    "summary": "funding source claimed but not yet documented",
                    "evidence_refs": [],
                }
            )
        return findings
