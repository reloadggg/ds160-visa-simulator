from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentRecord
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
    "normal": "补齐一套自洽的正常材料",
    "school_mismatch": "生成学校信息冲突材料",
    "sponsor_equity_gap": "生成父母股权/资金来源缺陷材料",
}


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
        requested_documents = interviewer_state.get("remaining_required_documents") or interviewer_state.get("requested_documents") or []
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
        applicant_name = str(profile.identity.get("full_name") or "LI, MINGHAO")
        passport_number = str(profile.identity.get("passport_number") or "E98765432")
        school_name = self._scenario_school_name(profile, scenario)
        program_name = str(profile.education.get("program_name") or "Master of Science in Computer Science")
        sevis_id = str(profile.education.get("sevis_id") or "N0034567890")
        scenario_label = DEBUG_FILL_SCENARIOS[scenario]

        if document_type == "relationship_proof_between_applicant_and_sponsors":
            return DebugFillDocument(
                document_type=document_type,
                filename="debug_relationship_proof.txt",
                text=(
                    "Household Register / Birth Relationship Certificate\n"
                    f"Applicant: {applicant_name}\n"
                    "Father: LI WEIGUO\n"
                    "Mother: ZHANG HUI\n"
                    "Relationship: LI WEIGUO is father of applicant; ZHANG HUI is mother of applicant.\n"
                    "Purpose: For F-1 student visa funding relationship verification.\n"
                ),
                fields={
                    "/identity/full_name": applicant_name,
                    "/funding/sponsor_relationship": "parents",
                    "/family/parent_names": "LI WEIGUO; ZHANG HUI",
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
                    "Nationality: China\n"
                ),
                fields={
                    "/identity/full_name": applicant_name,
                    "/identity/passport_number": passport_number,
                    "/identity/nationality": "China",
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
                    "Sponsor: LI WEIGUO and ZHANG HUI\n"
                    "Available funds: USD 68,000 equivalent.\n"
                    "Funding source: proceeds from family company equity transfer.\n"
                    "Company equity ownership: LI WEIGUO holds 38% shares in Minghao Trading Co., Ltd.\n"
                    "Missing: equity transfer agreement, company registration record, and tax/payment trail.\n"
                    f"Applicant: {applicant_name}\n"
                ),
                fields={
                    "/funding/primary_source": "parents",
                    "/funding/source_detail": "family company equity transfer",
                    "/funding/equity_ownership": "LI WEIGUO holds 38% shares in Minghao Trading Co., Ltd.",
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
                "Sponsor: LI WEIGUO and ZHANG HUI\n"
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
            return "University of Wisconsin-Madison"
        school_name = str(profile.education.get("school_name") or "").strip()
        return school_name or "New York University"

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
        self.evidence.replace_document_result(document.document_id, [chunk], evidence_items)
        self.db.add(document)
        self.db.flush()
        return document
