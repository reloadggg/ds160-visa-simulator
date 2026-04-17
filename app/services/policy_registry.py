from copy import deepcopy
from pathlib import Path

import yaml


class PolicyRegistry:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(
                f"Policy pack directory does not exist: {self.directory}"
            )
        self._cache = {
            path.stem: self._load_pack(path)
            for path in sorted(self.directory.glob("*.yaml"))
        }

    def list_families(self) -> list[str]:
        return sorted(self._cache.keys())

    def get(self, family: str) -> dict:
        return deepcopy(self._cache[family])

    def _load_pack(self, path: Path) -> dict:
        payload = yaml.safe_load(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"Policy pack payload must be a mapping: {path}")
        if payload.get("visa_family") != path.stem:
            raise ValueError(f"Policy pack visa_family mismatch: {path}")
        return payload
