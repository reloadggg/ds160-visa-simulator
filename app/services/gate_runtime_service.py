from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.domain.document_types import normalize_document_type
from app.domain.runtime import GateOverallStatus
from app.repositories.session_repo import SessionRepository


class GateRuntimeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sessions = SessionRepository(db)

    def refresh_session(self, session_id: str, *, save: bool = True) -> SessionRecord:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")
        return self.refresh_record(record, save=save)

    def refresh_record(self, record: SessionRecord, *, save: bool = True) -> SessionRecord:
        gate_status = dict(record.gate_status_json or {})
        required_documents = [
            dict(item) for item in gate_status.get("required_documents", [])
        ]
        if record.declared_family is None:
            record.phase_state = "intake"
            record.gate_status_json = {
                "declared_family": None,
                "scenario_key": None,
                "status": GateOverallStatus.FAMILY_NOT_SELECTED,
                "required_documents": [],
            }
            return self._persist(record, save=save)

        documents = self.db.scalars(
            select(DocumentRecord).where(DocumentRecord.session_id == record.session_id)
        ).all()
        jobs = self.db.scalars(
            select(JobRecord).where(JobRecord.session_id == record.session_id)
        ).all()

        has_waiting_parse = False
        all_required_ready = bool(required_documents)
        for item in required_documents:
            doc_type = item["document_type"]
            matched_documents = [
                document
                for document in documents
                if self._counts_toward_gate(document)
                and self._matches_document_type(document, doc_type)
            ]
            matched_jobs = [
                job
                for job in jobs
                if job.kind == "gate_parse"
                and any(
                    job.payload_json.get("document_id") == document.document_id
                    for document in matched_documents
                )
                and job.status in {"queued", "processing"}
            ]
            is_uploaded = bool(matched_documents)
            is_parsed = any(document.status == "parsed" for document in matched_documents)
            meets_minimum_fields = self._meets_minimum_fields(record, doc_type, is_parsed)

            if meets_minimum_fields:
                item["status"] = "ready"
            elif is_uploaded:
                item["status"] = "uploaded"
            else:
                item["status"] = "missing"

            item["is_uploaded"] = is_uploaded
            item["is_parsed"] = is_parsed
            item["meets_minimum_fields"] = meets_minimum_fields

            if not meets_minimum_fields:
                all_required_ready = False
            if is_uploaded and not is_parsed:
                has_waiting_parse = True
            if matched_jobs:
                has_waiting_parse = True

        if all_required_ready:
            gate_status["status"] = GateOverallStatus.READY_FOR_INTERVIEW
            record.phase_state = "interview"
        elif has_waiting_parse:
            gate_status["status"] = GateOverallStatus.WAITING_FOR_PARSE
            record.phase_state = "gate_review"
        else:
            gate_status["status"] = GateOverallStatus.PENDING_DOCUMENTS
            record.phase_state = "gate_review" if required_documents else "intake"

        gate_status["required_documents"] = required_documents
        record.gate_status_json = gate_status
        return self._persist(record, save=save)

    def build_gate_response(self, record: SessionRecord) -> dict:
        gate_status = record.gate_status_json or {}
        overall_status = gate_status.get("status", GateOverallStatus.PENDING_DOCUMENTS)
        gate_progress = self._build_gate_progress(gate_status)
        requested_documents = self._primary_requested_documents(
            gate_status.get("required_documents", [])
        )

        if overall_status == GateOverallStatus.FAMILY_NOT_SELECTED:
            return {
                "assistant_message": "当前处于材料门控阶段，请先选择签证家族。",
                "governor_decision": "need_more_evidence",
                "score_summary": {
                    "category_fit": 0,
                    "document_readiness": 0,
                    "narrative_consistency": 0,
                    "confidence": 0,
                },
                "requested_documents": [],
                "gate_progress": gate_progress,
            }

        if overall_status == GateOverallStatus.WAITING_FOR_PARSE:
            return {
                "assistant_message": (
                    "当前处于材料门控阶段。材料已提交，系统正在解析，"
                    "暂时还不能进入正式 interview。"
                ),
                "governor_decision": "need_more_evidence",
                "score_summary": {
                    "category_fit": 0,
                    "document_readiness": 0,
                    "narrative_consistency": 0,
                    "confidence": 0,
                },
                "requested_documents": requested_documents,
                "gate_progress": gate_progress,
            }

        return {
            "assistant_message": (
                "当前处于材料门控阶段。请先补齐必需材料，"
                "之后才能进入正式 interview。"
            ),
            "governor_decision": "need_more_evidence",
            "score_summary": {
                "category_fit": 0,
                "document_readiness": 0,
                "narrative_consistency": 0,
                "confidence": 0,
            },
            "requested_documents": requested_documents,
            "gate_progress": gate_progress,
        }

    def build_gate_support(self, record: SessionRecord) -> dict:
        gate_status = record.gate_status_json or {}
        gate_progress = self._build_gate_progress(gate_status)
        primary_document_item = self._pick_primary_document(
            gate_status.get("required_documents", [])
        )
        support_message = self._build_support_message(primary_document_item)

        return {
            "requested_documents": (
                []
                if primary_document_item is None
                else [primary_document_item["document_type"]]
            ),
            "primary_document": (
                None
                if primary_document_item is None
                else primary_document_item["document_type"]
            ),
            "support_message": support_message,
            "gate_progress": gate_progress,
        }

    def merge_interview_response(self, response: dict, record: SessionRecord) -> dict:
        support = self.build_gate_support(record)
        requested_documents = list(response.get("requested_documents", []))
        assistant_message = str(response.get("assistant_message", "")).strip()
        if not assistant_message and not requested_documents:
            assistant_message = support["support_message"] or ""
            requested_documents = list(support["requested_documents"])

        merged = dict(response)
        merged["assistant_message"] = assistant_message
        merged["requested_documents"] = requested_documents
        merged["gate_progress"] = support["gate_progress"]
        return merged

    def _build_gate_progress(self, gate_status: dict) -> dict:
        documents = []
        ready_count = 0
        uploaded_count = 0
        missing_count = 0

        for item in gate_status.get("required_documents", []):
            document_progress = {
                "document_type": item["document_type"],
                "status": item.get("status", "missing"),
                "is_uploaded": item.get("is_uploaded", False),
                "is_parsed": item.get("is_parsed", False),
                "meets_minimum_fields": item.get("meets_minimum_fields", False),
            }
            documents.append(document_progress)

            status = document_progress["status"]
            if status == "ready":
                ready_count += 1
            elif status == "uploaded":
                uploaded_count += 1
            else:
                missing_count += 1

        return {
            "overall_status": gate_status.get(
                "status", GateOverallStatus.PENDING_DOCUMENTS
            ),
            "ready_count": ready_count,
            "uploaded_count": uploaded_count,
            "missing_count": missing_count,
            "documents": documents,
        }

    def _matches_document_type(self, document: DocumentRecord, document_type: str) -> bool:
        artifact_json = document.artifact_json or {}
        artifact_document_type = normalize_document_type(
            artifact_json.get("document_type")
            or artifact_json.get("metadata", {}).get("document_type")
        )
        if artifact_document_type is not None:
            return artifact_document_type == normalize_document_type(document_type)

        filename = document.filename.lower()
        return document_type in filename

    def _counts_toward_gate(self, document: DocumentRecord) -> bool:
        artifact_json = document.artifact_json or {}
        if artifact_json.get("counts_toward_gate") is False:
            return False
        if artifact_json.get("metadata", {}).get("counts_toward_gate") is False:
            return False
        return True

    def _meets_minimum_fields(
        self,
        record: SessionRecord,
        document_type: str,
        is_parsed: bool,
    ) -> bool:
        if document_type != "funding_proof":
            return is_parsed
        if not is_parsed:
            return False

        profile_json = record.profile_json or {}
        field_state = (
            profile_json.get("field_states", {})
            .get("/funding/primary_source", {})
            .get("state")
        )
        evidence_refs = (
            profile_json.get("field_provenance", {})
            .get("/funding/primary_source", {})
            .get("evidence_refs", [])
        )
        return field_state == "documented" and bool(evidence_refs)

    def _pick_primary_document(self, required_documents: list[dict]) -> dict | None:
        for item in required_documents:
            if item.get("status") == "missing":
                return item
        for item in required_documents:
            if not item.get("meets_minimum_fields", False):
                return item
        return None

    def _build_support_message(self, primary_document_item: dict | None) -> str | None:
        if primary_document_item is None:
            return None

        document_type = primary_document_item["document_type"]
        if primary_document_item.get("status") == "uploaded":
            return f"当前最关键的证明是 {document_type}，系统正在等待解析结果。"
        return f"当前最缺的关键证明是 {document_type}。"

    def _primary_requested_documents(self, required_documents: list[dict]) -> list[str]:
        primary_document_item = self._pick_primary_document(required_documents)
        if primary_document_item is None:
            return []
        return [primary_document_item["document_type"]]

    def _persist(self, record: SessionRecord, *, save: bool) -> SessionRecord:
        if save:
            return self.sessions.save(record)
        self.db.add(record)
        self.db.flush()
        return record
