from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class InterviewerPromptRegistry:
    def __init__(self, prompt_dir: str | None = None) -> None:
        if prompt_dir is None:
            prompt_dir = str(Path(__file__).resolve().parents[1] / "interviewer_prompts")
        self.prompt_dir = Path(prompt_dir)

    def build_instructions(
        self,
        module_key: str,
        declared_family: str | None = None,
        prompt_pack_id: str | None = None,
        prompt_version: str | None = None,
    ) -> str:
        payload = self.load_prompt_payload(
            declared_family=declared_family,
            prompt_pack_id=prompt_pack_id,
            prompt_version=prompt_version,
        )
        stable_policy = payload.get("stable_policy", {})
        module_policies = payload.get("module_policies", {})
        family_policy = payload.get("family_policy", {})
        module_instructions = str(module_policies.get(module_key, "")).strip()

        blocks = [
            ("Prompt Pack", self._prompt_pack_header(payload)),
            ("稳定 System Policy", self._compose_policy_block(stable_policy)),
            ("签证家族 Policy", self._compose_policy_block(family_policy)),
            ("当前 Agent 任务", module_instructions),
            ("Fallback Policy", self._fallback_text(payload, module_key)),
        ]
        return "\n\n".join(
            f"【{title}】\n{content.strip()}"
            for title, content in blocks
            if isinstance(content, str) and content.strip()
        )

    def load_prompt_payload(
        self,
        declared_family: str | None = None,
        prompt_pack_id: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        payload = self._normalize_payload(self._read_yaml(self.prompt_dir / "base.yaml"))
        normalized_family = self._normalize_family_key(declared_family)
        if normalized_family is None:
            merged = payload
        else:
            family_path = self.prompt_dir / f"{normalized_family}.yaml"
            if not family_path.exists():
                merged = payload
            else:
                merged = self._deep_merge(
                    payload,
                    self._normalize_payload(self._read_yaml(family_path)),
                )

        if prompt_pack_id is not None:
            merged["prompt_pack_id"] = prompt_pack_id
        if prompt_version is not None:
            merged["prompt_version"] = prompt_version
        return merged

    def fallback_messages(
        self,
        declared_family: str | None = None,
        prompt_pack_id: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, str]:
        payload = self.load_prompt_payload(
            declared_family=declared_family,
            prompt_pack_id=prompt_pack_id,
            prompt_version=prompt_version,
        )
        fallback = payload.get("fallback", {})
        question_agent_fallback = fallback.get("question_agent", {})
        if not isinstance(question_agent_fallback, dict):
            return {}
        return {
            key: value.strip()
            for key, value in question_agent_fallback.items()
            if isinstance(value, str) and value.strip()
        }

    def _normalize_family_key(self, declared_family: str | None) -> str | None:
        if declared_family is None:
            return None
        normalized = declared_family.strip().lower()
        return normalized or None

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Prompt payload must be a mapping: {path}")
        return payload

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "stable_policy" in payload or "module_policies" in payload:
            normalized = deepcopy(payload)
            normalized.setdefault("prompt_pack_id", "ds160.default")
            normalized.setdefault("prompt_version", "v1")
            normalized.setdefault("stable_policy", {})
            normalized.setdefault("family_policy", {})
            normalized.setdefault("module_policies", {})
            normalized.setdefault("fallback", {})
            return normalized

        sections = payload.get("sections", {})
        modules = payload.get("modules", {})
        is_base_style_payload = any(
            sections.get(key) is not None
            for key in (
                "role",
                "interview_style",
                "output_rules",
                "future_case_slot",
            )
        )
        return {
            "prompt_pack_id": payload.get("prompt_pack_id", "ds160.default"),
            "prompt_version": payload.get("prompt_version", "v1"),
            "stable_policy": {
                key: value
                for key, value in {
                    "role": sections.get("role"),
                    "interview_style": sections.get("interview_style"),
                    "judgment_rules": sections.get("judgment_rules"),
                    "output_rules": sections.get("output_rules"),
                    "future_case_slot": sections.get("future_case_slot"),
                }.items()
                if value is not None
            }
            | (
                {
                    "judgment_rules": sections.get("judgment_rules"),
                }
                if is_base_style_payload and sections.get("judgment_rules") is not None
                else {}
            ),
            "family_policy": (
                {
                    "judgment_rules": sections.get("judgment_rules"),
                }
                if not is_base_style_payload and sections.get("judgment_rules") is not None
                else {}
            ),
            "module_policies": modules,
            "fallback": payload.get("fallback", {}),
        }

    def _prompt_pack_header(self, payload: dict[str, Any]) -> str:
        prompt_pack_id = payload.get("prompt_pack_id") or "ds160.default"
        prompt_version = payload.get("prompt_version") or "v1"
        return f"prompt_pack_id={prompt_pack_id}\nprompt_version={prompt_version}"

    def _compose_policy_block(self, policy_payload: Any) -> str | None:
        if not isinstance(policy_payload, dict):
            return None
        return "\n\n".join(
            f"{key}:\n{value.strip()}"
            for key, value in policy_payload.items()
            if isinstance(value, str) and value.strip()
        ) or None

    def _fallback_text(
        self,
        payload: dict[str, Any],
        module_key: str,
    ) -> str | None:
        fallback = payload.get("fallback", {})
        module_fallback = fallback.get(module_key)
        if not isinstance(module_fallback, dict):
            return None
        return "\n".join(
            f"{key}: {value.strip()}"
            for key, value in module_fallback.items()
            if isinstance(value, str) and value.strip()
        ) or None

    def _deep_merge(
        self,
        base: dict[str, Any],
        override: dict[str, Any],
    ) -> dict[str, Any]:
        merged = deepcopy(base)
        for key, value in override.items():
            if (
                isinstance(value, dict)
                and isinstance(merged.get(key), dict)
            ):
                append_strings = key in {
                    "family_policy",
                    "module_policies",
                    "stable_policy",
                }
                merged[key] = self._deep_merge_mapping(
                    merged[key],
                    value,
                    append_strings=append_strings,
                )
                continue
            merged[key] = deepcopy(value)
        return merged

    def _deep_merge_mapping(
        self,
        base: dict[str, Any],
        override: dict[str, Any],
        *,
        append_strings: bool,
    ) -> dict[str, Any]:
        merged = deepcopy(base)
        for key, value in override.items():
            current_value = merged.get(key)
            if isinstance(value, dict) and isinstance(current_value, dict):
                merged[key] = self._deep_merge_mapping(
                    current_value,
                    value,
                    append_strings=append_strings,
                )
                continue
            if append_strings and isinstance(value, str) and isinstance(current_value, str):
                base_text = current_value.strip()
                override_text = value.strip()
                if base_text and override_text:
                    merged[key] = f"{base_text}\n\n{override_text}"
                else:
                    merged[key] = override_text or base_text
                continue
            merged[key] = deepcopy(value)
        return merged
