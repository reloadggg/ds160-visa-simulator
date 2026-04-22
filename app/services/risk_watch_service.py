from __future__ import annotations

from typing import Any

from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState

FUNDING_QUESTION_MARKERS = (
    "fund",
    "funding",
    "pay",
    "tuition",
    "sponsor",
    "sponsoring",
    "financial",
    "bank",
    "education costs",
    "education expenses",
    "资助",
    "学费",
    "资金",
    "银行",
)
SCHOOL_QUESTION_MARKERS = (
    "school admitted",
    "which school",
    "which university",
    "admitted",
    "admission",
    "university",
    "college",
    "program",
    "education history",
    "education background",
    "academic background",
    "i-20",
    "sevis",
    "学校",
    "录取",
    "项目",
    "专业",
)
TRAVEL_PURPOSE_QUESTION_MARKERS = (
    "purpose of your travel",
    "why are you traveling",
    "why do you want to study",
    "purpose of your trip",
    "travel purpose",
    "目的",
    "赴美",
    "旅行目的",
)
HIGH_RISK_REVIEW_REASON_CODES = (
    "record_conflict",
    "unresolved_key_proof_gap",
    "evasive_answer",
)


class RiskWatchService:
    def high_risk_review_signal(
        self,
        profile: ApplicantProfile,
        score: ScoreState,
    ) -> RiskFlag | None:
        risk_watch = profile.ds160_view.get("risk_watch", {})
        evasive_turn_count = int(risk_watch.get("evasive_turn_count", 0))
        missing_key_proof_turn_count = int(
            risk_watch.get("missing_key_proof_turn_count", 0)
        )

        for reason_code in HIGH_RISK_REVIEW_REASON_CODES:
            risk_flag = next(
                (
                    item
                    for item in score.risk_flags
                    if item.code == reason_code
                    and item.severity == "high"
                    and item.evidence_refs
                ),
                None,
            )
            if risk_flag is None:
                continue
            if reason_code == "record_conflict":
                return risk_flag
            if reason_code == "evasive_answer" and evasive_turn_count >= 2:
                return risk_flag
            if (
                reason_code == "unresolved_key_proof_gap"
                and missing_key_proof_turn_count >= 2
            ):
                return risk_flag
        return None

    def apply_risk_watch_signals(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        history_turns: list[Any],
        message_text: str,
    ) -> None:
        risk_watch = dict(profile.ds160_view.get("risk_watch", {}))
        evasive_turn_count = int(risk_watch.get("evasive_turn_count", 0))
        missing_key_proof_turn_count = int(
            risk_watch.get("missing_key_proof_turn_count", 0)
        )

        current_focus = record.current_focus_json or {}
        focus_kind = current_focus.get("kind")
        focus_question = current_focus.get("question")
        focus_document = current_focus.get("document_type")

        if focus_kind == "interview_question" and focus_question:
            if self.is_evasive_answer(focus_question, message_text):
                evasive_turn_count += 1
            else:
                evasive_turn_count = 0

        if focus_kind == "required_document" and focus_document:
            if focus_document in score.missing_evidence:
                missing_key_proof_turn_count += 1
                if self.is_evasive_document_response(focus_document, message_text):
                    evasive_turn_count += 1
                else:
                    evasive_turn_count = 0
            else:
                missing_key_proof_turn_count = 0
                evasive_turn_count = 0

        risk_watch["evasive_turn_count"] = evasive_turn_count
        risk_watch["missing_key_proof_turn_count"] = missing_key_proof_turn_count
        profile.ds160_view["risk_watch"] = risk_watch

        latest_user_ref = self.latest_user_turn_ref(history_turns)
        if evasive_turn_count >= 2:
            self.upsert_risk_flag(
                score,
                code="evasive_answer",
                severity="high",
                status="supported",
                evidence_refs=[] if latest_user_ref is None else [latest_user_ref],
            )

        if missing_key_proof_turn_count >= 2:
            self.upsert_risk_flag(
                score,
                code="unresolved_key_proof_gap",
                severity="high",
                status="supported",
                evidence_refs=[] if latest_user_ref is None else [latest_user_ref],
            )

    def upsert_risk_flag(
        self,
        score: ScoreState,
        *,
        code: str,
        severity: str,
        status: str,
        evidence_refs: list[str],
    ) -> None:
        for risk_flag in score.risk_flags:
            if risk_flag.code != code:
                continue
            risk_flag.severity = severity
            risk_flag.status = status
            risk_flag.evidence_refs = list(evidence_refs)
            return

        score.risk_flags.append(
            RiskFlag(
                code=code,
                severity=severity,
                status=status,
                evidence_refs=list(evidence_refs),
            )
        )

    def latest_user_turn_ref(self, history_turns: list[Any]) -> str | None:
        turn_id = self.latest_user_turn_id(history_turns)
        if turn_id is None:
            return None
        return f"msg:{turn_id}"

    def latest_user_turn_id(self, history_turns: list[Any]) -> str | None:
        for turn in reversed(history_turns):
            if getattr(turn, "role", None) != "user":
                continue
            turn_id = getattr(turn, "turn_id", None)
            if isinstance(turn_id, str) and turn_id:
                return turn_id
        return None

    def is_evasive_answer(
        self,
        focus_question: str,
        message_text: str,
    ) -> bool:
        normalized = message_text.lower()
        evasive_markers = (
            "later",
            "not now",
            "move on",
            "another question",
            "school plan",
            "my major",
            "explain later",
            "let's talk about",
            "以后再说",
            "先不说",
            "换个问题",
            "学校计划",
            "专业",
        )
        if any(marker in normalized for marker in evasive_markers):
            return True

        question_topic = self.question_topic(focus_question)
        if question_topic == "funding":
            return not self.mentions_funding(message_text)
        if question_topic == "school":
            return not self.mentions_school_context(message_text)
        if question_topic == "travel_purpose":
            return not self.mentions_travel_purpose(message_text)
        return False

    def question_topic(self, focus_question: str) -> str | None:
        normalized = focus_question.lower()
        if any(marker in normalized for marker in FUNDING_QUESTION_MARKERS):
            return "funding"
        if any(marker in normalized for marker in SCHOOL_QUESTION_MARKERS):
            return "school"
        if any(marker in normalized for marker in TRAVEL_PURPOSE_QUESTION_MARKERS):
            return "travel_purpose"
        return None

    def is_evasive_document_response(
        self,
        focus_document: str,
        message_text: str,
    ) -> bool:
        normalized = message_text.lower()
        if any(
            token in normalized
            for token in (
                "upload",
                "provide",
                "submit",
                "proof",
                "document",
                "上传",
                "提供",
                "提交",
                "证明",
                "材料",
            )
        ):
            return False
        if focus_document == "funding_proof":
            return not self.mentions_funding(message_text)
        if focus_document == "passport_bio":
            return "passport" not in normalized
        return True

    def mentions_funding(self, message_text: str) -> bool:
        normalized = message_text.lower()
        funding_markers = (
            "parent",
            "parents",
            "mother",
            "father",
            "mom",
            "dad",
            "myself",
            "self",
            "self-funded",
            "self funded",
            "sponsor",
            "sponsoring",
            "uncle",
            "aunt",
            "scholarship",
            "bank",
            "savings",
            "financial",
            "funding",
            "pay",
            "cover",
            "tuition",
            "资助",
            "学费",
            "父母",
            "奖学金",
            "自己",
            "银行",
        )
        return any(marker in normalized for marker in funding_markers)

    def mentions_school_context(self, message_text: str) -> bool:
        normalized = message_text.lower()
        school_markers = (
            "school",
            "university",
            "college",
            "program",
            "admit",
            "admission",
            "i-20",
            "sevis",
            "学校",
            "录取",
            "项目",
            "专业",
        )
        return any(marker in normalized for marker in school_markers)

    def mentions_travel_purpose(self, message_text: str) -> bool:
        normalized = message_text.lower()
        purpose_markers = (
            "study",
            "student",
            "school",
            "degree",
            "education",
            "program",
            "学",
            "留学",
            "读书",
            "课程",
        )
        return any(marker in normalized for marker in purpose_markers)
