import os

import pytest

from app.agents.model_factory import AgentModelFactory


@pytest.mark.live_llm
def test_live_model_factory_builds_openai_compatible_model() -> None:
    assert os.getenv("OPENAI_API_KEY")
    assert os.getenv("OPENAI_BASE_URL")

    model, runtime = AgentModelFactory().build("extractor_agent", "interview_turn")

    assert model is not None
    assert runtime["provider"] == "openai_compatible"
    assert runtime["model"] == "gpt-5.4"
