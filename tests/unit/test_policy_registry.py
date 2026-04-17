from app.services.policy_registry import PolicyRegistry
from app.services.runtime_policies import RuntimePolicyRegistry


def test_policy_registry_loads_all_supported_families() -> None:
    registry = PolicyRegistry("app/policy_packs")

    families = registry.list_families()

    assert families == ["b1_b2", "f1", "h1b", "j1", "l1a", "l1b", "m1", "o1"]


def test_runtime_policy_registry_returns_scoring_engine_config() -> None:
    registry = RuntimePolicyRegistry("app/runtime_policies/default.yaml")

    policy = registry.get("scoring_engine", "interview_turn")

    assert policy["provider"] == "openai"
    assert policy["model"] == "gpt-5.2"
