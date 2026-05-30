from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentRecord
from app.domain.case_memory import (
    CaseClaim,
    DocumentTypeCandidate,
    EvidenceCard,
    InterviewNextMove,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
    ProofPoint,
)
from app.domain.contracts import ApplicantProfile
from app.domain.document_types import normalize_document_type
from app.domain.evidence import DocumentChunk, DocumentSourceType, EvidenceItem
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_repo import SessionRepository
from app.services.gate_runtime_service import GateRuntimeService
from app.services.message_service import MessageService
from app.services.profile_recompute_service import ProfileRecomputeService
from app.services.runtime_errors import ModelRuntimeError


@dataclass(frozen=True)
class DebugFillDocument:
    document_type: str
    filename: str
    text: str
    fields: dict[str, str]
    scenario: str
    scenario_label: str


DebugFillScenario = Literal[
    "normal",
    "school_mismatch",
    "sponsor_equity_gap",
]

DEBUG_FILL_SCENARIOS: dict[str, str] = {
    "normal": "生成当前缺口参考材料",
    "school_mismatch": "生成学校信息冲突材料",
    "sponsor_equity_gap": "生成父母股权/资金来源缺陷材料",
}

SYNTHETIC_APPLICANT_NAME = "TEST APPLICANT"
SYNTHETIC_PASSPORT_NUMBER = "X00000000"
SYNTHETIC_NATIONALITY = "EXAMPLELAND"
SYNTHETIC_SEVIS_ID = "N0000000000"
SYNTHETIC_SCHOOL_NAME = "Example University"
SYNTHETIC_CONFLICT_SCHOOL_NAME = "Alternate Example University"
SYNTHETIC_PROGRAM_NAME = "Example Degree Program"
SYNTHETIC_PARENT_NAMES = ("PARENT SPONSOR A", "PARENT SPONSOR B")
SYNTHETIC_COMPANY_NAME = "Example Family Business LLC"


def _explicit_list_field(payload: dict, key: str) -> list[str] | None:
    if key not in payload:
        return None
    return list(payload.get(key) or [])


class DebugFillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)
        self.documents = DocumentRepository(db)
        self.evidence = EvidenceRepository(db)

    def fill_current_gap(
        self,
        session_id: str,
        *,
        scenario: str = "normal",
    ) -> dict:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        fill_scenario = self._normalize_scenario(scenario)
        target_document_type = self._target_document_type(record, scenario=fill_scenario)
        profile = self._profile(record)
        fill_document = self._build_document(
            target_document_type,
            profile,
            scenario=fill_scenario,
        )
        document = self._create_parsed_document(record.session_id, fill_document)

        ProfileRecomputeService(self.db).recompute_session(record.session_id, save=False)
        GateRuntimeService(self.db).refresh_record(record, save=False)
        self.db.commit()
        main_flow_response: dict = {}
        refresh_error: str | None = None
        try:
            main_flow_response = MessageService(self.db).refresh_after_material_change(
                record.session_id,
                reason=f"debug_fill:{fill_document.document_type}",
            )
        except ModelRuntimeError as exc:
            refresh_error = exc.detail
            self.db.rollback()
        except Exception as exc:
            refresh_error = f"{exc.__class__.__name__}: {exc}"
            self.db.rollback()
        self.db.refresh(record)

        return {
            "session_id": record.session_id,
            "fill_scenario": fill_document.scenario,
            "fill_scenario_label": fill_document.scenario_label,
            "filled_document_type": fill_document.document_type,
            "filled_summary": self._filled_summary(fill_document),
            "document_id": document.document_id,
            "filename": document.filename,
            "phase_state": record.phase_state,
            "gate_status": record.gate_status_json,
            "assistant_message": main_flow_response.get("assistant_message"),
            "governor_decision": main_flow_response.get("governor_decision"),
            "requested_documents": list(
                main_flow_response.get("requested_documents", []) or []
            ),
            "remaining_required_documents": list(
                main_flow_response.get("remaining_required_documents", []) or []
            ),
            "turn_decision": dict(main_flow_response.get("turn_decision", {}) or {}),
            "runtime_view_state": dict(
                main_flow_response.get("runtime_view_state", {}) or {}
            ),
            "material_refresh": dict(
                main_flow_response.get("material_refresh", {}) or {}
            ),
            "main_flow_refresh_error": refresh_error,
        }

    def available_scenarios(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": label}
            for value, label in DEBUG_FILL_SCENARIOS.items()
        ]

    def _normalize_scenario(self, scenario: str) -> DebugFillScenario:
        normalized = scenario.strip().lower()
        if normalized in DEBUG_FILL_SCENARIOS:
            return normalized  # type: ignore[return-value]
        raise ValueError(f"unsupported debug fill scenario: {scenario}")

    def _target_document_type(
        self,
        record,
        *,
        scenario: DebugFillScenario = "normal",
    ) -> str:
        if scenario == "sponsor_equity_gap":
            return "funding_proof"

        current_focus = record.current_focus_json or {}
        focus_document = current_focus.get("document_type")
        if isinstance(focus_document, str) and focus_document.strip():
            return self._normalize_fill_document_type(focus_document)

        interviewer_state = record.interviewer_state_json or {}
        requested_documents = _explicit_list_field(
            interviewer_state,
            "remaining_required_documents",
        )
        if requested_documents is None:
            requested_documents = _explicit_list_field(
                interviewer_state,
                "requested_documents",
            )
        requested_documents = requested_documents or []
        if isinstance(requested_documents, list):
            for item in requested_documents:
                if isinstance(item, str) and item.strip():
                    return self._normalize_fill_document_type(item)

        gate_status = record.gate_status_json or {}
        for item in gate_status.get("required_documents", []):
            if isinstance(item, dict) and item.get("status") != "ready":
                document_type = item.get("document_type")
                if isinstance(document_type, str) and document_type.strip():
                    return self._normalize_fill_document_type(document_type)
        return "funding_proof"

    def _normalize_fill_document_type(self, value: str) -> str:
        normalized = normalize_document_type(value)
        if normalized in {
            "ds160",
            "passport_bio",
            "i20",
            "admission_letter",
            "funding_proof",
            "relationship_proof_between_applicant_and_sponsors",
        }:
            return normalized
        compact = value.lower()
        if "i-20" in compact or "i20" in compact:
            return "i20"
        if "录取" in compact or "admission" in compact:
            return "admission_letter"
        if "关系" in compact or "出生" in compact or "户口" in compact:
            return "relationship_proof_between_applicant_and_sponsors"
        if "资金" in compact or "股权" in compact or "equity" in compact:
            return "funding_proof"
        return normalized or value.strip()

    def _profile(self, record) -> ApplicantProfile:
        if record.profile_json:
            return ApplicantProfile.model_validate(record.profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{record.session_id}")

    def _build_document(
        self,
        document_type: str,
        profile: ApplicantProfile,
        *,
        scenario: DebugFillScenario,
    ) -> DebugFillDocument:
        applicant_name = self._profile_value(
            profile.identity,
            "full_name",
            SYNTHETIC_APPLICANT_NAME,
        )
        passport_number = self._profile_value(
            profile.identity,
            "passport_number",
            SYNTHETIC_PASSPORT_NUMBER,
        )
        nationality = self._profile_value(
            profile.identity,
            "nationality",
            SYNTHETIC_NATIONALITY,
        )
        parent_a, parent_b = self._parent_names(profile)
        school_name = self._scenario_school_name(profile, scenario)
        program_name = self._profile_value(
            profile.education,
            "program_name",
            SYNTHETIC_PROGRAM_NAME,
        )
        sevis_id = self._profile_value(
            profile.education,
            "sevis_id",
            SYNTHETIC_SEVIS_ID,
        )
        scenario_label = DEBUG_FILL_SCENARIOS[scenario]

        if document_type == "relationship_proof_between_applicant_and_sponsors":
            return DebugFillDocument(
                document_type=document_type,
                filename="debug_relationship_proof.txt",
                text=(
                    "Household Register / Birth Relationship Certificate\n"
                    f"Applicant: {applicant_name}\n"
                    f"Parent 1: {parent_a}\n"
                    f"Parent 2: {parent_b}\n"
                    f"Relationship: {parent_a} and {parent_b} are parents of applicant.\n"
                    "Purpose: For F-1 student visa funding relationship verification.\n"
                ),
                fields={
                    "/identity/full_name": applicant_name,
                    "/funding/sponsor_relationship": "parents",
                    "/family/parent_names": f"{parent_a}; {parent_b}",
                },
                scenario=scenario,
                scenario_label=scenario_label,
            )

        if document_type == "passport_bio":
            return DebugFillDocument(
                document_type=document_type,
                filename="debug_passport_bio.txt",
                text=(
                    "Passport Bio Page\n"
                    f"Full name: {applicant_name}\n"
                    f"Passport number: {passport_number}\n"
                    f"Nationality: {nationality}\n"
                ),
                fields={
                    "/identity/full_name": applicant_name,
                    "/identity/passport_number": passport_number,
                    "/identity/nationality": nationality,
                },
                scenario=scenario,
                scenario_label=scenario_label,
            )

        if document_type == "ds160":
            return DebugFillDocument(
                document_type=document_type,
                filename="debug_ds160_confirmation.txt",
                text=(
                    "DS-160 Confirmation Page\n"
                    f"Full name: {applicant_name}\n"
                    f"Passport number: {passport_number}\n"
                    "Travel purpose: STUDENT (F1)\n"
                ),
                fields={
                    "/identity/full_name": applicant_name,
                    "/identity/passport_number": passport_number,
                    "/visa_intent/travel_purpose": "STUDENT (F1)",
                },
                scenario=scenario,
                scenario_label=scenario_label,
            )

        if document_type == "i20":
            return DebugFillDocument(
                document_type=document_type,
                filename="debug_i20.txt",
                text=(
                    "Form I-20\n"
                    f"SEVIS ID: {sevis_id}\n"
                    f"School name: {school_name}\n"
                    f"Program: {program_name}\n"
                ),
                fields={
                    "/education/sevis_id": sevis_id,
                    "/education/school_name": school_name,
                    "/education/program_name": program_name,
                },
                scenario=scenario,
                scenario_label=scenario_label,
            )

        if document_type == "admission_letter":
            return DebugFillDocument(
                document_type=document_type,
                filename="debug_admission_letter.txt",
                text=(
                    "Admission Letter\n"
                    f"Student: {applicant_name}\n"
                    f"School name: {school_name}\n"
                    f"Program: {program_name}\n"
                    "Term: Fall 2026\n"
                ),
                fields={
                    "/education/school_name": school_name,
                    "/education/program_name": program_name,
                },
                scenario=scenario,
                scenario_label=scenario_label,
            )

        if scenario == "sponsor_equity_gap":
            return DebugFillDocument(
                document_type="funding_proof",
                filename="debug_parent_equity_gap_funding_proof.txt",
                text=(
                    "Parent Funding Proof\n"
                    "Primary source: parents\n"
                    f"Sponsor: {parent_a} and {parent_b}\n"
                    "Available funds: USD 68,000 equivalent.\n"
                    "Funding source: proceeds from family company equity transfer.\n"
                    f"Company equity ownership: {parent_a} holds 38% shares in {SYNTHETIC_COMPANY_NAME}.\n"
                    "Transfer received date: 2026-04-12.\n"
                    f"Applicant: {applicant_name}\n"
                ),
                fields={
                    "/funding/primary_source": "parents",
                    "/funding/source_detail": "family company equity transfer",
                    "/funding/equity_ownership": (
                        f"{parent_a} holds 38% shares in {SYNTHETIC_COMPANY_NAME}."
                    ),
                },
                scenario=scenario,
                scenario_label=scenario_label,
            )

        return DebugFillDocument(
            document_type="funding_proof",
            filename="debug_funding_proof.txt",
            text=(
                "Parent Funding Proof\n"
                "Primary source: parents\n"
                f"Sponsor: {parent_a} and {parent_b}\n"
                "Available funds: USD 68,000 equivalent.\n"
                f"Applicant: {applicant_name}\n"
            ),
            fields={"/funding/primary_source": "parents"},
            scenario=scenario,
            scenario_label=scenario_label,
        )

    def _scenario_school_name(
        self,
        profile: ApplicantProfile,
        scenario: DebugFillScenario,
    ) -> str:
        if scenario == "school_mismatch":
            claimed_school = self._profile_value(
                profile.education,
                "school_name",
                SYNTHETIC_SCHOOL_NAME,
            )
            if claimed_school == SYNTHETIC_CONFLICT_SCHOOL_NAME:
                return SYNTHETIC_SCHOOL_NAME
            return SYNTHETIC_CONFLICT_SCHOOL_NAME
        return self._profile_value(
            profile.education,
            "school_name",
            SYNTHETIC_SCHOOL_NAME,
        )

    def _parent_names(self, profile: ApplicantProfile) -> tuple[str, str]:
        raw_parent_names = profile.family_specific.get("parent_names")
        if isinstance(raw_parent_names, list):
            parents = [
                str(item).strip()
                for item in raw_parent_names
                if str(item).strip()
            ]
        elif isinstance(raw_parent_names, str):
            parents = [
                item.strip()
                for item in raw_parent_names.replace(" and ", ";").split(";")
                if item.strip()
            ]
        else:
            parents = []
        if len(parents) >= 2:
            return parents[0], parents[1]
        if len(parents) == 1:
            return parents[0], SYNTHETIC_PARENT_NAMES[1]
        return SYNTHETIC_PARENT_NAMES

    def _profile_value(
        self,
        payload: dict,
        key: str,
        fallback: str,
    ) -> str:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    def _filled_summary(self, fill_document: DebugFillDocument) -> str:
        field_labels = ", ".join(fill_document.fields.keys())
        return (
            f"{fill_document.scenario_label}；生成 {fill_document.filename}，"
            f"写入字段：{field_labels}。"
        )

    def _create_parsed_document(
        self,
        session_id: str,
        fill_document: DebugFillDocument,
    ) -> DocumentRecord:
        document = self.documents.create_document(
            session_id=session_id,
            filename=fill_document.filename,
            raw_bytes=fill_document.text.encode("utf-8"),
            raw_text=fill_document.text,
            artifact_json={
                "document_id": "pending",
                "session_id": session_id,
                "filename": fill_document.filename,
                "source_type": DocumentSourceType.TEXT.value,
                "parser_name": "debug_fill",
                "status": "parsed",
                "page_count": 1,
                "metadata": {
                    "debug_fill": True,
                    "debug_fill_scenario": fill_document.scenario,
                    "debug_fill_scenario_label": fill_document.scenario_label,
                    "document_type": fill_document.document_type,
                    "document_assessment": {
                        "document_type": fill_document.document_type,
                        "document_type_candidates": [fill_document.document_type],
                        "relevance": "high",
                        "supported_claims": list(fill_document.fields.keys()),
                        "confidence": 1.0,
                        "feedback_message": (
                            f"Debug fill generated this document for local testing: "
                            f"{fill_document.scenario_label}."
                        ),
                        "relevant": True,
                        "counts_toward_gate": True,
                    },
                    "counts_toward_gate": True,
                    "relevant": True,
                },
            },
        )
        document.status = "parsed"
        artifact = dict(document.artifact_json or {})
        artifact["document_id"] = document.document_id
        document.artifact_json = artifact

        chunk = DocumentChunk(
            chunk_id=f"chunk-{uuid4().hex[:12]}",
            document_id=document.document_id,
            session_id=session_id,
            ordinal=0,
            page_number=1,
            text=fill_document.text,
            metadata={
                "debug_fill": True,
                "debug_fill_scenario": fill_document.scenario,
            },
        )
        evidence_items = [
            EvidenceItem(
                evidence_id=f"evi-{uuid4().hex[:12]}",
                session_id=session_id,
                document_id=document.document_id,
                chunk_id=chunk.chunk_id,
                evidence_type=fill_document.document_type,
                field_path=field_path,
                value=value,
                excerpt=fill_document.text[:240],
                confidence=1.0,
                metadata={
                    "debug_fill": True,
                    "debug_fill_scenario": fill_document.scenario,
                },
            )
            for field_path, value in fill_document.fields.items()
        ]
        material_result = self._material_understanding_result(
            document=document,
            fill_document=fill_document,
            evidence_items=evidence_items,
        )
        artifact = dict(document.artifact_json or {})
        artifact["understanding_status"] = "completed"
        artifact["material_understanding_job"] = MaterialUnderstandingJob(
            job_id=f"debug-fill-{document.document_id}",
            document_id=document.document_id,
            status="completed",
            trigger="debug_bundle",
            result=material_result,
        ).model_dump(mode="json", exclude_none=True)
        artifact["material_understanding_result"] = material_result.model_dump(
            mode="json"
        )
        artifact["evidence_cards"] = [
            item.model_dump(mode="json") for item in material_result.evidence_cards
        ]
        artifact["case_board_delta"] = self._case_board_delta(
            document=document,
            fill_document=fill_document,
            result=material_result,
        )
        document.artifact_json = artifact
        self.evidence.replace_document_result(document.document_id, [chunk], evidence_items)
        self.db.add(document)
        self.db.flush()
        return document

    def _material_understanding_result(
        self,
        *,
        document: DocumentRecord,
        fill_document: DebugFillDocument,
        evidence_items: list[EvidenceItem],
    ) -> MaterialUnderstandingResult:
        evidence_cards = [
            EvidenceCard(
                evidence_id=item.evidence_id,
                source_type="debug_material",
                document_id=document.document_id,
                page_number=1,
                excerpt=item.excerpt,
                claim_refs=[self._claim_id(document.document_id, item.field_path)],
                confidence=item.confidence,
                metadata={
                    "filename": document.filename,
                    "field_path": item.field_path,
                    "debug_fill": True,
                    "debug_fill_scenario": fill_document.scenario,
                },
            )
            for item in evidence_items
        ]
        claims = [
            CaseClaim(
                claim_id=self._claim_id(document.document_id, item.field_path),
                field_path=item.field_path,
                value=item.value,
                status="documented",
                supporting_evidence_ids=[item.evidence_id],
                confidence=item.confidence,
                metadata={
                    "document_id": document.document_id,
                    "filename": document.filename,
                    "debug_fill": True,
                },
            )
            for item in evidence_items
        ]
        proof_points = [
            ProofPoint(
                proof_point_id=f"proof-{document.document_id}-{index}",
                visa_family="unknown",
                question=f"请说明 {field_path} 如何支持你的签证计划。",
                status="supported",
                why_it_matters="调试材料提供了一个可核验的案例事实。",
                claim_refs=[claim.claim_id],
                evidence_refs=list(claim.supporting_evidence_ids),
                metadata={"debug_fill": True},
            )
            for index, (field_path, claim) in enumerate(
                zip(fill_document.fields.keys(), claims)
            )
        ]
        return MaterialUnderstandingResult(
            document_type_candidates=[
                DocumentTypeCandidate(
                    document_type=fill_document.document_type,
                    confidence=1.0,
                )
            ],
            evidence_cards=evidence_cards,
            extracted_claims=claims,
            proof_points=proof_points,
            suggested_followups=[
                InterviewNextMove(
                    move_type="ask",
                    question="材料已经加入案例理解。请继续说明它和你的签证计划有什么关系。",
                    reason="调试材料已作为案例证据写入 Case Memory。",
                    claim_refs=[claim.claim_id for claim in claims[:3]],
                    evidence_refs=[item.evidence_id for item in evidence_cards[:3]],
                )
            ],
            confidence=1.0,
        )

    def _case_board_delta(
        self,
        *,
        document: DocumentRecord,
        fill_document: DebugFillDocument,
        result: MaterialUnderstandingResult,
    ) -> dict:
        return {
            "latest_material": {
                "document_id": document.document_id,
                "filename": document.filename,
                "understanding_status": "completed",
                "document_type": fill_document.document_type,
                "document_type_candidates": [
                    item.model_dump(mode="json")
                    for item in result.document_type_candidates
                ],
                "supported_claims": list(fill_document.fields.keys()),
                "confidence": result.confidence,
                "unknowns": [],
            },
            "evidence_cards": [
                item.model_dump(mode="json") for item in result.evidence_cards
            ],
            "claims": [item.model_dump(mode="json") for item in result.extracted_claims],
            "open_proof_points": [
                item.model_dump(mode="json") for item in result.proof_points
            ],
            "conflicts": [],
            "next_move": result.suggested_followups[0].model_dump(mode="json"),
        }

    def _claim_id(self, document_id: str, field_path: str) -> str:
        normalized = field_path.strip("/").replace("/", "-").replace("_", "-")
        return f"claim-{document_id}-{normalized or 'unknown'}"
