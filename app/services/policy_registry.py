from pathlib import Path

import yaml


class PolicyRegistry:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)
        self._cache = {
            path.stem: yaml.safe_load(path.read_text())
            for path in sorted(self.directory.glob("*.yaml"))
        }

    def list_families(self) -> list[str]:
        return sorted(self._cache.keys())

    def get(self, family: str) -> dict:
        return self._cache[family]
