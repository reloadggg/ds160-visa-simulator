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
        normalized = message_text.lower()
        self.client.generate_json(
            module_key="extractor_service",
            stage_key="gate_review",
            payload={"message_text": message_text},
        )
        profile.ds160_view["last_user_message"] = message_text
        if "parent" in normalized:
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED,
            )
            profile.funding["primary_source"] = "parents"
        return profile
