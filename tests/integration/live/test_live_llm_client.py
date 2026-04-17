import os

import pytest

from app.integrations.llm_client import LLMClient


@pytest.mark.live_llm
def test_live_llm_client_returns_runtime_metadata() -> None:
    assert os.getenv("OPENAI_API_KEY")
    assert os.getenv("OPENAI_BASE_URL")

    payload = LLMClient().generate_json(
        module_key="extractor_service",
        stage_key="gate_review",
        payload={"message_text": "My parents will pay for my studies."},
    )

    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-5.4"
    assert payload["response_json"] is not None
