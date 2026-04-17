import json
from pathlib import Path

import httpx

from app.core.settings import settings
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
        result = {
            "module_key": module_key,
            "stage_key": stage_key,
            "provider": runtime["provider"],
            "model": runtime["model"],
            "prompt_template_id": runtime.get("prompt_template_id"),
            "prompt_version": runtime.get("prompt_version"),
            "payload": payload,
        }
        if not settings.openai_api_key or not settings.openai_base_url:
            result["response_json"] = None
            return result

        request_payload = {
            "model": runtime["model"],
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return valid JSON only. "
                        "Reply with an object containing keys "
                        "module_key, stage_key, and echoed_payload."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "module_key": module_key,
                            "stage_key": stage_key,
                            "payload": payload,
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
            "temperature": 0,
            "stream": True,
        }
        content_parts: list[str] = []
        raw_chunks: list[dict] = []
        with httpx.stream(
            "POST",
            self._chat_completions_url(),
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=settings.openai_timeout_seconds,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                raw_chunks.append(chunk)
                delta = chunk["choices"][0].get("delta", {})
                chunk_content = delta.get("content")
                if chunk_content:
                    content_parts.append(chunk_content)

        content = "".join(content_parts)

        try:
            parsed_content = json.loads(content)
        except json.JSONDecodeError:
            parsed_content = {"raw_text": content}

        result["raw_response"] = raw_chunks
        result["response_json"] = parsed_content
        return result

    def _chat_completions_url(self) -> str:
        base_url = settings.openai_base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"
