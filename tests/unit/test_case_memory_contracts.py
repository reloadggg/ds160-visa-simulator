from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.case_memory import (
    CaseClaim,
    CaseMemorySnapshot,
    DocumentTypeCandidate,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
    ProofPoint,
)


def test_material_understanding_job_accepts_ai_native_result() -> None:
    result = MaterialUnderstandingResult(
        document_type_candidates=[
            DocumentTypeCandidate(document_type="i20", confidence=0.91)
        ],
        evidence_cards=[
            EvidenceCard(
                evidence_id="ev-school",
                source_type="uploaded_file",
                document_id="doc-i20",
                page_number=1,
                excerpt="School Name: Example University",
                claim_refs=["claim-school"],
                confidence=0.93,
            )
        ],
        extracted_claims=[
            CaseClaim(
                claim_id="claim-school",
                field_path="/education/school_name",
                value="Example University",
                status="documented",
                supporting_evidence_ids=["ev-school"],
                confidence=0.93,
            )
        ],
        proof_points=[
            ProofPoint(
                proof_point_id="proof-program-match",
                visa_family="f1",
                question="The student must explain why this program fits their plan.",
                status="partial",
                why_it_matters="Program fit is central to F-1 intent.",
                claim_refs=["claim-school"],
                evidence_refs=["ev-school"],
            )
        ],
        suggested_followups=[
            InterviewNextMove(
                move_type="ask",
                question="为什么选择 Example University 的这个项目？",
                reason="I-20 已证明学校，下一步需要核验学习动机。",
                claim_refs=["claim-school"],
                evidence_refs=["ev-school"],
            )
        ],
        confidence=0.9,
    )

    job = MaterialUnderstandingJob(
        job_id="job-1",
        document_id="doc-i20",
        status="completed",
        result=result,
    )

    assert job.trigger == "upload"
    assert job.result is not None
    assert job.result.evidence_cards[0].source_type == "uploaded_file"


def test_documented_claim_requires_supporting_evidence() -> None:
    with pytest.raises(ValidationError, match="documented claims require"):
        CaseClaim(
            claim_id="claim-school",
            field_path="/education/school_name",
            value="Example University",
            status="documented",
        )


def test_material_understanding_rejects_unknown_evidence_references() -> None:
    with pytest.raises(ValidationError, match="unknown evidence ids"):
        MaterialUnderstandingResult(
            extracted_claims=[
                CaseClaim(
                    claim_id="claim-school",
                    field_path="/education/school_name",
                    value="Example University",
                    status="documented",
                    supporting_evidence_ids=["ev-missing"],
                )
            ]
        )


def test_case_memory_snapshot_requires_unique_ids() -> None:
    with pytest.raises(ValidationError, match="claim ids must be unique"):
        CaseMemorySnapshot(
            claims=[
                CaseClaim(
                    claim_id="claim-1",
                    field_path="/identity/full_name",
                    status="unknown",
                ),
                CaseClaim(
                    claim_id="claim-1",
                    field_path="/identity/passport_number",
                    status="unknown",
                ),
            ]
        )
