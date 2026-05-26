from __future__ import annotations

from typing import Any

INTERVIEW_MEMORY_KEY = "interview_memory"

QUESTION_TOPIC_LABELS = {
    "academic_preparation": "学术准备",
    "funding": "资金来源",
    "post_study_plan": "毕业后计划",
    "program_school": "学校和项目选择",
}


class InterviewMemoryService:
    """Deterministic oral interview memory derived from public turns."""

    def annotate_user_answer(
        self,
        *,
        assistant_turn: Any | None,
        user_turn: Any,
    ) -> dict[str, Any]:
        user_answer = self._string_or_none(getattr(user_turn, "content", None)) or ""
        question = self._assistant_question(assistant_turn)
        topic = self.question_topic(question)
        if topic is None:
            topic = self.answer_topic(user_answer)
        if topic is None:
            return {}

        status = "answered" if self._answer_has_content(user_answer) else "non_answer"
        payload = {
            "schema_version": "interview_memory.v1",
            "kind": "oral_answer",
            "topic": topic,
            "topic_label": QUESTION_TOPIC_LABELS.get(topic, topic),
            "status": status,
            "closed": status == "answered",
            "question_turn_id": self._string_or_none(
                getattr(assistant_turn, "turn_id", None)
            ),
            "question_turn_index": getattr(assistant_turn, "turn_index", None),
            "question": question,
            "answer_turn_id": self._string_or_none(getattr(user_turn, "turn_id", None)),
            "answer_turn_index": getattr(user_turn, "turn_index", None),
            "answer_excerpt": self._excerpt(user_answer),
            "confidence": 0.74 if status == "answered" else 0.4,
        }
        return {key: value for key, value in payload.items() if value is not None}

    def build_memory(self, turns: list[dict[str, Any]]) -> dict[str, Any]:
        answered_topics: dict[str, dict[str, Any]] = {}
        topic_history: list[dict[str, Any]] = []
        last_assistant: dict[str, Any] | None = None

        for turn in turns:
            role = self._string_or_none(turn.get("role"))
            if role == "assistant":
                last_assistant = turn
                continue
            if role != "user":
                continue

            metadata_memory = self._payload(
                self._payload(turn.get("metadata")).get(INTERVIEW_MEMORY_KEY)
            )
            memory_item = metadata_memory or self._memory_from_normalized_turns(
                last_assistant,
                turn,
            )
            topic = self._string_or_none(memory_item.get("topic"))
            if not topic:
                continue
            item = {
                "topic": topic,
                "topic_label": QUESTION_TOPIC_LABELS.get(topic, topic),
                "status": self._string_or_none(memory_item.get("status")) or "answered",
                "closed": bool(memory_item.get("closed", True)),
                "question_turn_id": self._string_or_none(
                    memory_item.get("question_turn_id")
                ),
                "question_turn_index": memory_item.get("question_turn_index"),
                "question": self._string_or_none(memory_item.get("question")),
                "answer_turn_id": self._string_or_none(memory_item.get("answer_turn_id"))
                or self._string_or_none(turn.get("turn_id")),
                "answer_turn_index": memory_item.get("answer_turn_index")
                or turn.get("turn_index"),
                "answer_excerpt": self._string_or_none(
                    memory_item.get("answer_excerpt")
                )
                or self._excerpt(self._string_or_none(turn.get("content")) or ""),
                "confidence": memory_item.get("confidence", 0.7),
            }
            item = {key: value for key, value in item.items() if value is not None}
            topic_history.append(item)
            if item.get("closed"):
                answered_topics[topic] = item

        return {
            "schema_version": "interview_memory.v1",
            "answered_topics": [
                answered_topics[key] for key in sorted(answered_topics)
            ],
            "answered_topic_keys": sorted(answered_topics),
            "topic_history": topic_history[-20:],
        }

    def question_topic(self, value: str | None) -> str | None:
        return self._topic(value)

    def answer_topic(self, value: str | None) -> str | None:
        return self._topic(value)

    def _memory_from_normalized_turns(
        self,
        assistant_turn: dict[str, Any] | None,
        user_turn: dict[str, Any],
    ) -> dict[str, Any]:
        question = self._string_or_none(
            assistant_turn.get("content") if assistant_turn else None
        )
        answer = self._string_or_none(user_turn.get("content")) or ""
        topic = self.question_topic(question) or self.answer_topic(answer)
        if topic is None:
            return {}
        status = "answered" if self._answer_has_content(answer) else "non_answer"
        return {
            "topic": topic,
            "status": status,
            "closed": status == "answered",
            "question_turn_id": self._string_or_none(
                assistant_turn.get("turn_id") if assistant_turn else None
            ),
            "question_turn_index": assistant_turn.get("turn_index")
            if assistant_turn
            else None,
            "question": question,
            "answer_turn_id": self._string_or_none(user_turn.get("turn_id")),
            "answer_turn_index": user_turn.get("turn_index"),
            "answer_excerpt": self._excerpt(answer),
            "confidence": 0.68,
        }

    def _assistant_question(self, assistant_turn: Any | None) -> str | None:
        if assistant_turn is None:
            return None
        metadata = self._payload(getattr(assistant_turn, "metadata_json", None))
        turn_record = self._payload(metadata.get("turn_record"))
        focus = self._payload(turn_record.get("focus"))
        return self._string_or_none(focus.get("question")) or self._string_or_none(
            getattr(assistant_turn, "content", None)
        )

    def _topic(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if any(
            marker in normalized
            for marker in (
                "第一年费用",
                "学费",
                "生活费",
                "资金",
                "资助",
                "父亲",
                "母亲",
                "父母",
                "fund",
                "sponsor",
                "tuition",
                "pay",
            )
        ):
            return "funding"
        if any(
            marker in normalized
            for marker in (
                "毕业后",
                "回国",
                "工作",
                "岗位",
                "任教",
                "career",
                "job",
                "return home",
                "post-graduation",
            )
        ):
            return "post_study_plan"
        if any(
            marker in normalized
            for marker in (
                "为什么选择",
                "为什么不在国内",
                "学校",
                "项目",
                "专业",
                "attend",
                "program",
                "school",
                "major",
                "university",
            )
        ):
            return "program_school"
        if any(
            marker in normalized
            for marker in (
                "本科",
                "成绩",
                "语言",
                "经历",
                "academic",
                "gpa",
                "toefl",
                "ielts",
                "experience",
            )
        ):
            return "academic_preparation"
        return None

    def _answer_has_content(self, value: str) -> bool:
        normalized = value.strip()
        if len(normalized) < 2:
            return False
        non_answers = (
            "不知道",
            "不清楚",
            "没想好",
            "资料里有",
            "材料里有",
            "already answered",
        )
        return not any(marker in normalized.casefold() for marker in non_answers)

    def _excerpt(self, value: str, limit: int = 220) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 3]}..."

    def _payload(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
