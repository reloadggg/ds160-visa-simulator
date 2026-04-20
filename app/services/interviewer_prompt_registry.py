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
    ) -> str:
        payload = self.load_prompt_payload(declared_family)
        sections = payload.get("sections", {})
        modules = payload.get("modules", {})
        module_instructions = modules[module_key].strip()

        blocks = [
            ("角色提示词", sections.get("role")),
            ("面谈风格提示词", sections.get("interview_style")),
            ("判断规则提示词", sections.get("judgment_rules")),
            ("输出方式提示词", sections.get("output_rules")),
            ("未来案例参考位", sections.get("future_case_slot")),
            ("当前 Agent 任务", module_instructions),
        ]
        return "\n\n".join(
            f"【{title}】\n{content.strip()}"
            for title, content in blocks
            if isinstance(content, str) and content.strip()
        )

    def load_prompt_payload(self, declared_family: str | None = None) -> dict[str, Any]:
        payload = self._read_yaml(self.prompt_dir / "base.yaml")
        normalized_family = self._normalize_family_key(declared_family)
        if normalized_family is None:
            return payload

        family_path = self.prompt_dir / f"{normalized_family}.yaml"
        if not family_path.exists():
            return payload
        return self._deep_merge(payload, self._read_yaml(family_path))

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
                merged[key] = self._deep_merge(merged[key], value)
                continue
            merged[key] = deepcopy(value)
        return merged
