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
        self.client.generate_json(
            module_key="extractor_service",
            stage_key="gate_review",
            payload={"message_text": message_text},
        )
        if "parent" in message_text.lower():
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED,
            )
            profile.funding["primary_source"] = "parents"
        return profile
