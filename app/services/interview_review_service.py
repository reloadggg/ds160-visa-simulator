from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.review_agent import InterviewReviewAgentRunner
from app.agents.schemas import AgentRuntimeDeps, InterviewReviewReport
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.evidence_service import EvidenceService
from app.services.report_service import ReportService
from app.services.retrieval_service import RetrievalService
from app.services.session_read_model_service import SessionReadModelService
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService


class InterviewReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.document_repo = DocumentRepository(db)
        self.read_model_service = SessionReadModelService(db)
        self.model_factory = AgentModelFactory()

    def generate(self, session_id: str) -> dict[str, Any]:
        record = self.session_repo.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        review_context = self._build_review_context(record)
        model, runtime = self._build_review_model(record.declared_family)
        if model is None:
            report = self._fallback_report(review_context)
            return self._response_payload(
                report=report,
                runtime=runtime,
                source="fallback",
                review_context=review_context,
            )

        try:
            report = InterviewReviewAgentRunner(
                model=model,
                instructions=runtime.get("instructions")
                or self.model_factory.build_instructions(
                    "review_agent",
                    declared_family=record.declared_family,
                ),
            ).run(
                deps=AgentRuntimeDeps(
                    session_id=session_id,
                    retrieval=RetrievalService(self.db),
                    evidence=EvidenceService(self.db),
                    policy_retrieval=VisaPolicyRetrievalService(),
                ),
                review_context=review_context,
            )
            return self._response_payload(
                report=report,
                runtime=runtime,
                source="llm",
                review_context=review_context,
            )
        except Exception as exc:
            runtime = dict(runtime)
            runtime["review_generation_error"] = str(exc)
            report = self._fallback_report(review_context)
            return self._response_payload(
                report=report,
                runtime=runtime,
                source="fallback",
                review_context=review_context,
            )

    def _build_review_context(self, record: Any) -> dict[str, Any]:
        read_model = self.read_model_service.build_from_record(record)
        report_service = ReportService()
        user_report = report_service.user_report(
            session_id=record.session_id,
            visa_family=record.declared_family or "unknown",
            governor_decision=record.current_governor_decision,
            profile_json=record.profile_json,
            phase_state=record.phase_state,
            gate_status=record.gate_status_json,
            runtime_view_state=read_model.runtime_view_state.model_dump(mode="json"),
            interviewer_state_json=record.interviewer_state_json,
            current_focus_json=record.current_focus_json,
        )
        internal_report = report_service.internal_report(
            session_id=record.session_id,
            visa_family=record.declared_family or "unknown",
            governor_decision=record.current_governor_decision,
            profile_json=record.profile_json,
            runtime_ledger=read_model.runtime_ledger.model_dump(mode="json"),
            runtime_view_state=read_model.runtime_view_state.model_dump(mode="json"),
            runtime_trace=record.runtime_trace_json,
            score_history=record.score_history_json,
            governor_history=record.governor_history_json,
            interviewer_state_json=record.interviewer_state_json,
            current_focus_json=record.current_focus_json,
        )
        documents = self.document_repo.list_session_document_exports(record.session_id)
        return {
            "session": {
                "session_id": record.session_id,
                "phase_state": record.phase_state,
                "declared_family": record.declared_family,
                "current_governor_decision": record.current_governor_decision,
                "gate_status": record.gate_status_json,
                "current_focus": record.current_focus_json,
            },
            "user_report": user_report,
            "internal_report": internal_report,
            "profile_snapshot": record.profile_json,
            "documents": [
                {
                    "document_id": document.document_id,
                    "filename": document.filename,
                    "status": document.status,
                    "extracted_text": document.raw_text or "",
                    "artifact": document.artifact_json or {},
                }
                for document in documents
            ],
        }

    def _build_review_model(
        self,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "review_agent",
                "post_interview",
                declared_family=declared_family,
            )
        except TypeError as exc:
            if "declared_family" not in str(exc):
                raise
            return self.model_factory.build("review_agent", "post_interview")

    def _fallback_report(self, review_context: dict[str, Any]) -> InterviewReviewReport:
        user_report = dict(review_context.get("user_report") or {})
        session = dict(review_context.get("session") or {})
        documents = list(review_context.get("documents") or [])
        missing_evidence = [
            item.get("name") or item.get("code")
            for item in list(user_report.get("missing_evidence") or [])
            if isinstance(item, dict)
        ]
        risk_points = list(user_report.get("risk_points") or [])
        recommended_improvements = list(user_report.get("recommended_improvements") or [])
        status = str(user_report.get("interview_status") or session.get("phase_state") or "unknown")
        is_refusal = status == "simulated_refusal"
        outcome = "模拟拒签复盘" if is_refusal else "阶段性面签复盘"
        outcome_reason = str(user_report.get("summary") or "当前面签已结束，基于现有记录生成复盘。")
        document_findings = [
            f"{document.get('filename')}：{document.get('status', '已上传')}"
            for document in documents[:5]
            if isinstance(document, dict)
        ]
        if not document_findings:
            document_findings = ["当前没有可用于复盘的已上传材料。"]

        return InterviewReviewReport(
            outcome=outcome,
            outcome_reason=outcome_reason,
            executive_summary=(
                "这次模拟的主要问题集中在关键条件或证据闭环不足。"
                if is_refusal or missing_evidence or risk_points
                else "这次模拟尚未形成明确拒签点，但仍需要继续补强回答和材料闭环。"
            ),
            strengths=list(user_report.get("strengths") or [])[:5]
            or ["已完成部分问答和材料提交，可作为下一轮练习基础。"],
            refusal_or_risk_reasons=(risk_points[:6] or ([outcome_reason] if is_refusal else [])),
            missing_or_weak_evidence=missing_evidence[:6]
            or ["暂无明确缺失材料，但仍建议核对核心材料是否覆盖签证类型要求。"],
            conversation_issues=[
                "回答需要更直接，优先说明事实、资金来源、学习计划和回国约束。"
            ],
            document_findings=document_findings,
            improvement_plan=recommended_improvements[:6]
            or ["按当前缺口补齐关键材料后，再进行一轮完整模拟。"],
            next_practice_focus=[
                "用 1-2 句话回答签证官问题，不展开成材料清单。",
                "确保每个关键说法都能被材料或 OCR 文本支持。",
            ],
        )

    def _response_payload(
        self,
        *,
        report: InterviewReviewReport,
        runtime: dict[str, Any],
        source: str,
        review_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "ds160.interview_review.v1",
            "source": source,
            "runtime": {
                "provider": runtime.get("provider"),
                "model": runtime.get("model"),
                "reasoning_effort": runtime.get("reasoning_effort"),
                "prompt_pack_id": runtime.get("prompt_pack_id"),
                "prompt_version": runtime.get("prompt_version"),
                "model_unavailable_reason": runtime.get("model_unavailable_reason"),
                "model_unavailable_missing_env_vars": runtime.get("model_unavailable_missing_env_vars", []),
                "review_generation_error": runtime.get("review_generation_error"),
            },
            "report": report.model_dump(mode="json"),
            "basis": {
                "session": review_context.get("session", {}),
                "document_count": len(list(review_context.get("documents") or [])),
                "has_internal_report": bool(review_context.get("internal_report")),
            },
        }
