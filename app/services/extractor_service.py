from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.integrations.llm_client import LLMClient


class ExtractorService:
    def __init__(self) -> None:
        self.client = LLMClient()

    def apply_message(
        self,
        profile: ApplicantProfile,
        message_text: str,
    ) -> ApplicantProfile:
        runtime_payload = self.client.generate_json(
            module_key="extractor_service",
            stage_key="gate_review",
            payload={"message_text": message_text},
        )
        profile.ds160_view["last_user_message"] = message_text

        funding_source = self._llm_funding_primary_source(
            runtime_payload.get("response_json"),
        )
        if funding_source is not None:
            self._apply_claimed_funding_source(profile, funding_source)
            return profile

        normalized = message_text.lower()
        if "parent" in normalized:
            self._apply_claimed_funding_source(profile, "parents")
        return profile

    def _llm_funding_primary_source(self, response_json: dict | None) -> str | None:
        if not isinstance(response_json, dict):
            return None

        funding_source = response_json.get("funding_primary_source")
        if funding_source is None:
            funding_payload = response_json.get("funding")
            if isinstance(funding_payload, dict):
                funding_source = funding_payload.get("primary_source")

        if not isinstance(funding_source, str):
            return None

        normalized = funding_source.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"parent", "parents", "mother", "father", "mom", "dad"}:
            return "parents"
        return None

    def _apply_claimed_funding_source(
        self,
        profile: ApplicantProfile,
        funding_source: str,
    ) -> None:
        profile.field_states["/funding/primary_source"] = FieldStateRecord(
            state=FieldState.CLAIMED,
        )
        profile.funding["primary_source"] = funding_source
