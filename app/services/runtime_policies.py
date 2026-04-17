from pathlib import Path

import yaml


class RuntimePolicyRegistry:
    def __init__(self, path: str) -> None:
        self.payload = yaml.safe_load(Path(path).read_text())

    def get(self, module_key: str, stage_key: str) -> dict:
        return self.payload[module_key][stage_key]
