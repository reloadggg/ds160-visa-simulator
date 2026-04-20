import re

from app.agents.schemas import ConsistencyFinding
from app.domain.contracts import ApplicantProfile, FieldState

HARD_CONFLICT_PATTERNS = (
    r"\bi lied\b",
    r"\bi (?:used|submitted|provided|uploaded|brought) fake\b",
    r"\bi (?:forged|forge|faked)\b",
)
DOCUMENT_BACKED_FIELDS: dict[str, tuple[str, str]] = {
    "/funding/primary_source": ("funding", "primary_source"),
    "/identity/full_name": ("identity", "full_name"),
    "/identity/passport_number": ("identity", "passport_number"),
    "/identity/nationality": ("identity", "nationality"),
    "/visa_intent/travel_purpose": ("visa_intent", "travel_purpose"),
    "/education/sevis_id": ("education", "sevis_id"),
    "/education/school_name": ("education", "school_name"),
    "/education/program_name": ("education", "program_name"),
    "/education/sponsor_name": ("education", "sponsor_name"),
}
FUNDING_FIELD_PATH = "/funding/primary_source"
FUNDING_PATTERNS = {
    "parents": ("parent", "parents", "mother", "father", "mom", "dad"),
    "self": (
        "myself",
        "self-funded",
        "self funded",
        "self-funding",
        "self funding",
        "my own savings",
        "personal savings",
        "my savings",
        "own savings",
        "personal funds",
        "i will pay",
        "i can pay",
        "i am paying",
        "i will pay myself",
        "i will cover",
        "i can cover",
        "i am covering",
        "i will fund",
    ),
    "relative": ("uncle", "aunt", "relative", "cousin", "brother", "sister"),
    "scholarship": ("scholarship", "assistantship", "stipend", "grant"),
}
NEGATION_MARKERS = ("not", "no", "never", "rather than", "instead of", "不是", "不靠")
CORRECTION_MARKERS = (
    "actually",
    "to clarify",
    "correction",
    "let me correct",
    "sorry",
    "more accurately",
    "i mean",
    "更正",
    "澄清",
    "准确地说",
    "其实",
)
PROOF_MARKERS = (
    "document",
    "documents",
    "proof",
    "evidence",
    "bank statement",
    "passport",
    "i20",
    "sevis",
    "材料",
    "证明",
    "银行",
)


class ConsistencyService:
    def evaluate(self, profile: ApplicantProfile) -> list[ConsistencyFinding]:
        findings: list[ConsistencyFinding] = []
        hard_conflict_ref = self._find_hard_conflict_evidence_ref(profile)
        if hard_conflict_ref is not None:
            findings.append(
                ConsistencyFinding(
                    finding_type="hard_conflict",
                    severity="high",
                    status="confirmed",
                    summary="applicant self-reported false or fraudulent record",
                    evidence_refs=[hard_conflict_ref],
                )
            )

        field_histories = self._sync_field_claim_histories(profile)
        funding_history = field_histories.get(FUNDING_FIELD_PATH, [])
        if funding_history:
            profile.ds160_view["funding_claim_history"] = funding_history[-6:]

        for field_path, claim_history in field_histories.items():
            finding = self._build_field_conflict_finding(profile, field_path, claim_history)
            if finding is not None:
                findings.append(finding)

        if (
            profile.funding.get("primary_source") == "parents"
            and not profile.field_provenance["/funding/primary_source"].evidence_refs
        ):
            findings.append(
                ConsistencyFinding(
                    finding_type="gap",
                    severity="medium",
                    status="supported",
                    summary="funding source claimed but not yet documented",
                    evidence_refs=[],
                )
            )
        return findings

    def _sync_field_claim_histories(
        self,
        profile: ApplicantProfile,
    ) -> dict[str, list[dict]]:
        archived_store = profile.ds160_view.setdefault("field_claim_history", {})
        histories: dict[str, list[dict]] = {}

        for field_path in DOCUMENT_BACKED_FIELDS:
            history = self._normalize_archived_claims(archived_store.get(field_path, []))
            if field_path == FUNDING_FIELD_PATH:
                history = self._merge_funding_turn_claims(profile, history)
            if not history:
                continue
            archived_store[field_path] = history
            histories[field_path] = history
        return histories

    def _normalize_archived_claims(self, items: object) -> list[dict]:
        if not isinstance(items, list):
            return []

        normalized: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            self._append_unique_claim(
                normalized,
                {
                    "turn_id": item.get("turn_id"),
                    "turn_index": item.get("turn_index"),
                    "value": value.strip(),
                    "content": str(item.get("content", "")),
                    "source": item.get("source", "claim_history"),
                },
            )
        return normalized

    def _merge_funding_turn_claims(
        self,
        profile: ApplicantProfile,
        history: list[dict],
    ) -> list[dict]:
        turn_history = profile.ds160_view.get("turn_history", [])
        if not isinstance(turn_history, list):
            return history

        merged = list(history)
        for turn in turn_history:
            if not isinstance(turn, dict) or turn.get("role") != "user":
                continue
            content = str(turn.get("content", ""))
            claim_value = self._parse_funding_claim_value(content)
            if claim_value is None:
                continue
            turn_id = turn.get("turn_id")
            turn_index = turn.get("turn_index")
            self._append_unique_claim(
                merged,
                {
                    "turn_id": turn_id,
                    "turn_index": turn_index,
                    "value": claim_value,
                    "content": content,
                    "source": turn.get("source", "turn_history"),
                },
            )
        return merged

    def _build_field_conflict_finding(
        self,
        profile: ApplicantProfile,
        field_path: str,
        claim_history: list[dict],
    ) -> ConsistencyFinding | None:
        document_value = self._document_backed_field_value(profile, field_path)
        if document_value is None or not claim_history:
            return None

        latest_claim = claim_history[-1]
        prior_conflicts = [
            item for item in claim_history[:-1] if item.get("value") != document_value
        ]
        evidence_refs = self._document_backed_field_refs(profile, field_path)
        latest_turn_ref = self._build_turn_evidence_ref(latest_claim)
        if latest_turn_ref not in evidence_refs:
            evidence_refs.append(latest_turn_ref)

        if latest_claim.get("value") == document_value:
            if not prior_conflicts:
                return None
            if self._is_reasonable_correction(latest_claim, prior_conflicts):
                return None
            prior_conflict_values = {item.get("value") for item in prior_conflicts}
            return ConsistencyFinding(
                finding_type="record_conflict",
                severity="medium" if len(prior_conflict_values) <= 1 else "high",
                status="supported",
                summary=self._aligned_conflict_summary(field_path),
                evidence_refs=evidence_refs,
            )

        conflicting_claims = [
            item for item in claim_history if item.get("value") != document_value
        ]
        if not conflicting_claims:
            return None

        distinct_conflicts = {item.get("value") for item in conflicting_claims}
        return ConsistencyFinding(
            finding_type="record_conflict",
            severity="high" if len(distinct_conflicts) >= 2 else "medium",
            status="supported",
            summary=self._conflict_summary(field_path),
            evidence_refs=evidence_refs,
        )

    def _document_backed_field_value(
        self,
        profile: ApplicantProfile,
        field_path: str,
    ) -> str | None:
        field_state = profile.field_states.get(field_path)
        provenance = profile.field_provenance.get(field_path)
        section, key = DOCUMENT_BACKED_FIELDS[field_path]
        container = getattr(profile, section)
        if (
            field_state is not None
            and field_state.state in {FieldState.DOCUMENTED, FieldState.CONFIRMED}
            and provenance is not None
            and provenance.evidence_refs
            and container.get(key)
        ):
            return str(container[key])

        snapshot = profile.ds160_view.get("document_evidence_snapshot", {}).get(field_path, {})
        value = snapshot.get("value")
        return str(value) if value else None

    def _document_backed_field_refs(
        self,
        profile: ApplicantProfile,
        field_path: str,
    ) -> list[str]:
        provenance = profile.field_provenance.get(field_path)
        if provenance is not None and provenance.evidence_refs:
            return list(provenance.evidence_refs)

        snapshot = profile.ds160_view.get("document_evidence_snapshot", {}).get(field_path, {})
        evidence_refs = snapshot.get("evidence_refs", [])
        return [str(item) for item in evidence_refs if isinstance(item, str)]

    def _parse_funding_claim_value(self, content: str) -> str | None:
        normalized = content.lower()
        matches = self._funding_claim_matches(normalized)
        if not matches:
            return None
        return matches[-1]["value"]

    def _funding_claim_matches(self, content: str) -> list[dict[str, int | str]]:
        matches: list[dict[str, int | str]] = []
        for clause_match in re.finditer(r"[^,.;!?\n]+", content):
            clause = clause_match.group(0)
            clause_offset = clause_match.start()
            for value, markers in FUNDING_PATTERNS.items():
                for marker in markers:
                    for marker_match in self._iter_marker_matches(clause, marker):
                        index = marker_match.start()
                        if not self._is_negated(clause, index, len(marker_match.group(0))):
                            matches.append(
                                {
                                    "value": value,
                                    "index": clause_offset + index,
                                }
                            )
        matches.sort(key=lambda item: int(item["index"]))
        return matches

    def _append_unique_claim(self, history: list[dict], claim: dict) -> None:
        existing = self._find_equivalent_claim(history, claim)
        if existing is None:
            history.append(claim)
            return
        if existing.get("turn_id") is None and claim.get("turn_id") is not None:
            existing.update(claim)

    def _find_equivalent_claim(self, history: list[dict], claim: dict) -> dict | None:
        for existing in history:
            if self._claims_equivalent(existing, claim):
                return existing
        return None

    def _claims_equivalent(self, left: dict, right: dict) -> bool:
        left_turn_id = left.get("turn_id")
        right_turn_id = right.get("turn_id")
        if (
            isinstance(left_turn_id, str)
            and left_turn_id
            and isinstance(right_turn_id, str)
            and right_turn_id
            and left_turn_id == right_turn_id
        ):
            return True

        left_turn_index = left.get("turn_index")
        right_turn_index = right.get("turn_index")
        if (
            isinstance(left_turn_index, int)
            and isinstance(right_turn_index, int)
            and left_turn_index == right_turn_index
        ):
            return True

        if left.get("value") != right.get("value"):
            return False

        left_content = self._normalized_claim_content(left)
        right_content = self._normalized_claim_content(right)
        if left_content and right_content and left_content == right_content:
            return True
        if left_content == str(left.get("value", "")).strip().casefold():
            return True
        if right_content == str(right.get("value", "")).strip().casefold():
            return True
        return False

    def _normalized_claim_content(self, claim: dict) -> str:
        return str(claim.get("content", "")).strip().casefold()

    def _count_claim_hits(self, content: str, markers: tuple[str, ...]) -> int:
        hits = 0
        for marker in markers:
            for marker_match in self._iter_marker_matches(content, marker):
                index = marker_match.start()
                if not self._is_negated(content, index, len(marker_match.group(0))):
                    hits += 1
        return hits

    def _iter_marker_matches(self, content: str, marker: str):
        pattern = re.compile(rf"(?<!\w){re.escape(marker)}(?!\w)")
        return pattern.finditer(content)

    def _is_negated(self, content: str, index: int, marker_length: int) -> bool:
        leading_window = content[max(0, index - 18) : index]
        if any(marker in leading_window for marker in NEGATION_MARKERS):
            return True

        trailing_window = content[index + marker_length : index + marker_length + 24].strip()
        return any(
            re.search(pattern, trailing_window) is not None
            for pattern in (
                r"^(?:is|are|am|was|were|will|would|can|could|do|does|did|has|have|had)\s+not\b",
                r"^(?:is|are|am|was|were|will|would|can|could|do|does|did|has|have|had)\s+no longer\b",
                r"^no longer\b",
            )
        )

    def _is_reasonable_correction(
        self,
        latest_claim: dict,
        prior_conflicts: list[dict],
    ) -> bool:
        if len(prior_conflicts) != 1:
            return False
        normalized = str(latest_claim.get("content", "")).lower()
        return any(marker in normalized for marker in CORRECTION_MARKERS) or any(
            marker in normalized for marker in PROOF_MARKERS
        )

    def _conflict_summary(self, field_path: str) -> str:
        if field_path == FUNDING_FIELD_PATH:
            return "oral funding explanation conflicts with documented evidence"
        return f"oral explanation conflicts with documented evidence for {field_path}"

    def _aligned_conflict_summary(self, field_path: str) -> str:
        if field_path == FUNDING_FIELD_PATH:
            return "oral funding explanation changed repeatedly before aligning with documented evidence"
        return f"oral explanation changed repeatedly before aligning with documented evidence for {field_path}"

    def _find_hard_conflict_evidence_ref(self, profile: ApplicantProfile) -> str | None:
        turn_history = profile.ds160_view.get("turn_history", [])
        if isinstance(turn_history, list):
            for turn in reversed(turn_history):
                if not isinstance(turn, dict) or turn.get("role") != "user":
                    continue
                content = str(turn.get("content", "")).lower()
                if self._contains_hard_conflict(content):
                    return self._build_turn_evidence_ref(turn)

        last_user_message = str(profile.ds160_view.get("last_user_message", "")).lower()
        if self._contains_hard_conflict(last_user_message):
            return "msg:last_user_turn"
        return None

    def _contains_hard_conflict(self, text: str) -> bool:
        return any(re.search(pattern, text) is not None for pattern in HARD_CONFLICT_PATTERNS)

    def _build_turn_evidence_ref(self, turn: dict) -> str:
        turn_id = turn.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            return f"msg:{turn_id}"

        turn_index = turn.get("turn_index")
        if isinstance(turn_index, int):
            return f"msg:turn_index:{turn_index}"

        return "msg:last_user_turn"
