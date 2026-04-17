from copy import deepcopy
from pathlib import Path

import yaml


class RuntimePolicyRegistry:
    def __init__(self, path: str) -> None:
        self.payload = yaml.safe_load(Path(path).read_text())
        if not isinstance(self.payload, dict):
            raise ValueError("Runtime policy payload must be a mapping")

    def get(self, module_key: str, stage_key: str) -> dict:
        return deepcopy(self.payload[module_key][stage_key])
