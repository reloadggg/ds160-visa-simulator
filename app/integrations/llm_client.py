from pathlib import Path

from app.services.runtime_policies import RuntimePolicyRegistry


class LLMClient:
    def __init__(self, runtime_policy_path: str | None = None) -> None:
        if runtime_policy_path is None:
            runtime_policy_path = str(
                Path(__file__).resolve().parents[1]
                / "runtime_policies"
                / "default.yaml"
            )
        self.registry = RuntimePolicyRegistry(runtime_policy_path)

    def generate_json(self, module_key: str, stage_key: str, payload: dict) -> dict:
        runtime = self.registry.get(module_key, stage_key)
        return {
            "module_key": module_key,
            "stage_key": stage_key,
            "provider": runtime["provider"],
            "model": runtime["model"],
            "prompt_template_id": runtime.get("prompt_template_id"),
            "prompt_version": runtime.get("prompt_version"),
            "payload": payload,
        }
