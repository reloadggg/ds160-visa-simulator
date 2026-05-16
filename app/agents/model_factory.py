from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.agents.user_model_config import current_user_model_config
from app.services.interviewer_prompt_registry import InterviewerPromptRegistry
from app.services.runtime_policies import RuntimePolicyRegistry


EMPTY_RUNTIME: dict[str, Any] = {
    "provider": None,
    "model": None,
    "reasoning_effort": None,
    "prompt_template_id": None,
    "prompt_pack_id": None,
    "prompt_version": None,
    "model_unavailable_reason": None,
    "model_unavailable_missing_env_vars": [],
    "model_unavailable_detail": None,
}
SUPPORTED_PROVIDERS = {"openai", "openai_compatible"}


class AgentModelFactory:
    def __init__(
        self,
        runtime_policy_path: str | None = None,
        prompt_dir: str | None = None,
    ) -> None:
        if runtime_policy_path is None:
            runtime_policy_path = str(
                Path(__file__).resolve().parents[1]
                / "runtime_policies"
                / "default.yaml"
            )
        self.registry = RuntimePolicyRegistry(runtime_policy_path)
        self.prompt_registry = InterviewerPromptRegistry(prompt_dir=prompt_dir)

    def build(
        self,
        module_key: str,
        stage_key: str,
        declared_family: str | None = None,
    ) -> tuple[OpenAIChatModel | None, dict[str, Any]]:
        try:
            runtime = self.registry.get(module_key, stage_key)
        except KeyError:
            return None, dict(EMPTY_RUNTIME)

        if module_key.endswith("_agent"):
            prompt_payload = self.prompt_registry.load_prompt_payload(
                declared_family=declared_family,
                prompt_pack_id=runtime.get("prompt_pack_id"),
                prompt_version=runtime.get("prompt_version"),
            )
            runtime["prompt_pack_id"] = prompt_payload.get("prompt_pack_id")
            runtime["prompt_version"] = prompt_payload.get(
                "prompt_version",
                runtime.get("prompt_version"),
            )
            runtime["fallback_messages"] = self.prompt_registry.fallback_messages(
                declared_family=declared_family,
                prompt_pack_id=runtime.get("prompt_pack_id"),
                prompt_version=runtime.get("prompt_version"),
            )
            runtime["instructions"] = self.prompt_registry.build_instructions(
                module_key,
                declared_family=declared_family,
                prompt_pack_id=runtime.get("prompt_pack_id"),
                prompt_version=runtime.get("prompt_version"),
            )

        if runtime.get("provider") not in SUPPORTED_PROVIDERS:
            return None, runtime

        user_config = current_user_model_config()
        api_key = user_config.api_key if user_config is not None else os.getenv("OPENAI_API_KEY")
        base_url = user_config.base_url if user_config is not None else os.getenv("OPENAI_BASE_URL")
        if user_config is not None:
            runtime["model"] = user_config.model
            runtime["provider"] = "openai_compatible"
            runtime["user_model_configured"] = True
        missing_env_vars = [
            env_var
            for env_var, value in (
                ("USER_MODEL_API_KEY", api_key)
                if user_config is not None
                else ("OPENAI_API_KEY", api_key),
                ("USER_MODEL_BASE_URL", base_url)
                if user_config is not None
                else ("OPENAI_BASE_URL", base_url),
            )
            if not value
        ]

        if missing_env_vars:
            runtime["model_unavailable_reason"] = "missing_openai_config"
            runtime["model_unavailable_missing_env_vars"] = missing_env_vars
            runtime["model_unavailable_detail"] = (
                "当前后端未配置可用的对话模型，无法生成面签问答。"
                f"请检查 {', '.join(missing_env_vars)}。"
            )
            return None, runtime

        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        model = OpenAIChatModel(runtime["model"], provider=provider)
        return model, runtime

    def build_instructions(
        self,
        module_key: str,
        declared_family: str | None = None,
    ) -> str:
        return self.prompt_registry.build_instructions(
            module_key,
            declared_family=declared_family,
        )
