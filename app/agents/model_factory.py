from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.services.runtime_policies import RuntimePolicyRegistry


EMPTY_RUNTIME: dict[str, Any] = {
    "provider": None,
    "model": None,
    "reasoning_effort": None,
    "prompt_template_id": None,
    "prompt_version": None,
}
SUPPORTED_PROVIDERS = {"openai", "openai_compatible"}


class AgentModelFactory:
    def __init__(self, runtime_policy_path: str | None = None) -> None:
        if runtime_policy_path is None:
            runtime_policy_path = str(
                Path(__file__).resolve().parents[1]
                / "runtime_policies"
                / "default.yaml"
            )
        self.registry = RuntimePolicyRegistry(runtime_policy_path)

    def build(
        self,
        module_key: str,
        stage_key: str,
    ) -> tuple[OpenAIChatModel | None, dict[str, Any]]:
        try:
            runtime = self.registry.get(module_key, stage_key)
        except KeyError:
            return None, dict(EMPTY_RUNTIME)

        if runtime.get("provider") not in SUPPORTED_PROVIDERS:
            return None, runtime

        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")

        if not api_key or not base_url:
            return None, runtime

        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        model = OpenAIChatModel(runtime["model"], provider=provider)
        return model, runtime
