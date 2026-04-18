from pathlib import Path

from app.domain.runtime import build_initial_gate_status
from app.services.policy_registry import PolicyRegistry


class GateService:
    def __init__(self) -> None:
        policy_pack_dir = Path(__file__).resolve().parents[1] / "policy_packs"
        self.registry = PolicyRegistry(str(policy_pack_dir))

    def default_scenario_package(self, family: str) -> tuple[str, list[str]]:
        pack = self.registry.get(family)
        scenarios = pack["scenarios"]
        if not scenarios:
            raise ValueError(f"policy pack has no scenarios: {family}")
        scenario_key = next(iter(scenarios))
        return scenario_key, scenarios[scenario_key]["required_initial_package"]

    def initial_gate_status(self, declared_family: str | None) -> dict:
        if declared_family is None:
            return build_initial_gate_status(
                declared_family=None,
                scenario_key=None,
                required_documents=[],
            )

        scenario_key, required_documents = self.default_scenario_package(declared_family)
        return build_initial_gate_status(
            declared_family=declared_family,
            scenario_key=scenario_key,
            required_documents=required_documents,
        )

    def required_package(
        self,
        family: str,
        scenario_key: str | None = None,
    ) -> list[str]:
        if scenario_key is None:
            scenario_key, _required_documents = self.default_scenario_package(family)
        pack = self.registry.get(family)
        return pack["scenarios"][scenario_key]["required_initial_package"]
