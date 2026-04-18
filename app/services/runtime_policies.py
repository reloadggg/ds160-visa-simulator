import os
import re
from copy import deepcopy
from pathlib import Path

import yaml


_OVERRIDABLE_FIELDS = (
    "provider",
    "model",
    "reasoning_effort",
    "prompt_template_id",
    "prompt_version",
)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).upper()


class RuntimePolicyRegistry:
    def __init__(self, path: str) -> None:
        self.payload = yaml.safe_load(Path(path).read_text())
        if not isinstance(self.payload, dict):
            raise ValueError("Runtime policy payload must be a mapping")

    def get(self, module_key: str, stage_key: str) -> dict:
        runtime = deepcopy(self.payload[module_key][stage_key])
        for field in _OVERRIDABLE_FIELDS:
            module_override = os.getenv(
                f"RUNTIME_{_normalize_key(module_key)}_{_normalize_key(stage_key)}_{_normalize_key(field)}"
            )
            default_override = os.getenv(
                f"RUNTIME_DEFAULT_{_normalize_key(field)}"
            )
            override = module_override or default_override
            if override is not None:
                runtime[field] = override
        return runtime
