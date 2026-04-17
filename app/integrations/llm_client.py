import json
from pathlib import Path
from typing import Any

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
            "reasoning_effort": runtime.get("reasoning_effort"),
            "prompt_template_id": runtime.get("prompt_template_id"),
            "prompt_version": runtime.get("prompt_version"),
            "payload": payload,
        }
        if not settings.openai_api_key or not settings.openai_base_url:
            result["response_json"] = None
            return result

        request_payload = {
            "model": runtime["model"],
            "messages": self._build_messages(module_key, stage_key, payload),
            "temperature": 0,
            "stream": True,
        }
        reasoning_effort = runtime.get("reasoning_effort")
        if reasoning_effort:
            request_payload["reasoning_effort"] = reasoning_effort

        content, raw_chunks = self._stream_chat_completion(request_payload)
        result["raw_response"] = raw_chunks
        result["response_json"] = self._parse_response_content(content)
        return result

    def _build_messages(
        self,
        module_key: str,
        stage_key: str,
        payload: dict,
    ) -> list[dict[str, str]]:
        prompt_key = f"{module_key}:{stage_key}"
        serialized_payload = json.dumps(payload, ensure_ascii=True)

        if prompt_key == "extractor_service:gate_review":
            return [
                {
                    "role": "system",
                    "content": (
                        "You extract structured visa interview evidence from one user "
                        "message. Return valid JSON only with keys "
                        "funding_primary_source, field_state, needs_followup, and "
                        "notes. Allowed funding_primary_source values for this v1 are "
                        '"parents" or null. Use "parents" only when the user clearly '
                        "states parents, mother, father, mom, or dad will pay or cover "
                        "costs. If the payer is undecided, unknown, or not stated, use "
                        'null and field_state="unknown". Do not infer unsupported facts.'
                    ),
                },
                {
                    "role": "user",
                    "content": serialized_payload,
                },
            ]

        if prompt_key == "scoring_engine:interview_turn":
            return [
                {
                    "role": "system",
                    "content": (
                        "You score one DS-160 simulator interview turn. Return valid "
                        "JSON only with integer keys category_fit, document_readiness, "
                        "narrative_consistency, confidence, and array key "
                        "missing_evidence_suggestions. All scores must be integers from "
                        "0 to 100. Missing evidence must stay unknown rather than false. "
                        "A low score alone cannot imply refusal. If findings show a "
                        "claimed funding source without documentation, reduce document "
                        "readiness and include funding_proof in "
                        "missing_evidence_suggestions."
                    ),
                },
                {
                    "role": "user",
                    "content": serialized_payload,
                },
            ]

        return [
            {
                "role": "system",
                "content": (
                    "Return valid JSON only. Reply with an object containing keys "
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
        ]

    def _stream_chat_completion(
        self,
        request_payload: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        retryable_errors = (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        )
        for attempt in range(2):
            content_parts: list[str] = []
            raw_chunks: list[dict[str, Any]] = []
            try:
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
                return "".join(content_parts), raw_chunks
            except retryable_errors:
                if attempt == 1:
                    raise

        return "", []

    def _parse_response_content(self, content: str) -> dict[str, Any]:
        normalized = content.strip()
        if not normalized:
            return {"raw_text": content}

        candidates = [normalized]
        stripped = self._strip_markdown_code_fence(normalized)
        if stripped != normalized:
            candidates.append(stripped)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
            return {"raw_text": candidate}

        return {"raw_text": normalized}

    def _strip_markdown_code_fence(self, content: str) -> str:
        if not content.startswith("```"):
            return content
        lines = content.splitlines()
        if not lines:
            return content
        body = lines[1:]
        if body and body[-1].strip() == "```":
            body = body[:-1]
        return "\n".join(body).strip()

    def _chat_completions_url(self) -> str:
        base_url = settings.openai_base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"
