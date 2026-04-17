from pathlib import Path

from app.services.policy_registry import PolicyRegistry


class GateService:
    def __init__(self) -> None:
        policy_pack_dir = Path(__file__).resolve().parents[1] / "policy_packs"
        self.registry = PolicyRegistry(str(policy_pack_dir))

    def required_package(
        self,
        family: str,
        scenario_key: str = "parent_sponsored",
    ) -> list[str]:
        pack = self.registry.get(family)
        return pack["scenarios"][scenario_key]["required_initial_package"]
