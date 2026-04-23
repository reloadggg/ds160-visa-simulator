import pytest

from app.services.policy_registry import PolicyRegistry
from app.services.runtime_policies import RuntimePolicyRegistry


def test_policy_registry_loads_all_supported_families() -> None:
    registry = PolicyRegistry("app/policy_packs")

    families = registry.list_families()

    assert families == ["b1_b2", "f1", "h1b", "j1", "l1a", "l1b", "m1", "o1"]


def test_runtime_policy_registry_returns_scoring_engine_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNTIME_DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv(
        "RUNTIME_SCORING_ENGINE_INTERVIEW_TURN_PROVIDER",
        raising=False,
    )
    registry = RuntimePolicyRegistry("app/runtime_policies/default.yaml")

    policy = registry.get("scoring_engine", "interview_turn")

    assert policy["provider"] == "openai"
    assert policy["model"] == "gpt-5.4"
    assert policy["reasoning_effort"] == "xhigh"


def test_policy_registry_raises_for_missing_directory(tmp_path) -> None:
    missing_directory = tmp_path / "missing"

    with pytest.raises(FileNotFoundError, match="Policy pack directory does not exist"):
        PolicyRegistry(str(missing_directory))


def test_policy_registry_get_returns_defensive_copy() -> None:
    registry = PolicyRegistry("app/policy_packs")

    policy = registry.get("f1")
    policy["scenarios"]["parent_sponsored"]["required_initial_package"].append(
        "unexpected_document"
    )

    fresh_policy = registry.get("f1")

    assert "unexpected_document" not in fresh_policy["scenarios"]["parent_sponsored"][
        "required_initial_package"
    ]


def test_runtime_policy_registry_get_returns_defensive_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNTIME_DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv(
        "RUNTIME_SCORING_ENGINE_INTERVIEW_TURN_PROVIDER",
        raising=False,
    )
    registry = RuntimePolicyRegistry("app/runtime_policies/default.yaml")

    policy = registry.get("scoring_engine", "interview_turn")
    policy["provider"] = "other"

    fresh_policy = registry.get("scoring_engine", "interview_turn")

    assert fresh_policy["provider"] == "openai"


def test_runtime_policy_registry_applies_default_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "RUNTIME_SCORING_AGENT_INTERVIEW_TURN_REASONING_EFFORT",
        raising=False,
    )
    monkeypatch.setenv("RUNTIME_DEFAULT_MODEL", "gpt-5.4")
    monkeypatch.setenv("RUNTIME_DEFAULT_REASONING_EFFORT", "high")

    registry = RuntimePolicyRegistry("app/runtime_policies/default.yaml")

    policy = registry.get("scoring_agent", "interview_turn")

    assert policy["model"] == "gpt-5.4"
    assert policy["reasoning_effort"] == "high"


def test_runtime_policy_registry_prefers_module_stage_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_DEFAULT_REASONING_EFFORT", "medium")
    monkeypatch.setenv(
        "RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_REASONING_EFFORT",
        "xhigh",
    )

    registry = RuntimePolicyRegistry("app/runtime_policies/default.yaml")

    policy = registry.get("question_agent", "interview_turn")

    assert policy["reasoning_effort"] == "xhigh"


def test_runtime_policy_registry_allows_provider_override_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_PROVIDER",
        "openai_compatible",
    )

    registry = RuntimePolicyRegistry("app/runtime_policies/default.yaml")

    policy = registry.get("question_agent", "interview_turn")

    assert policy["provider"] == "openai_compatible"


def test_policy_registry_raises_for_invalid_pack_payload(tmp_path) -> None:
    policy_pack_directory = tmp_path / "policy_packs"
    policy_pack_directory.mkdir()
    (policy_pack_directory / "f1.yaml").write_text("- invalid\n")

    with pytest.raises(ValueError, match="must be a mapping"):
        PolicyRegistry(str(policy_pack_directory))


def test_runtime_policy_registry_raises_for_invalid_top_level_payload(
    tmp_path,
) -> None:
    runtime_policy_path = tmp_path / "runtime.yaml"
    runtime_policy_path.write_text("- invalid\n")

    with pytest.raises(ValueError, match="must be a mapping"):
        RuntimePolicyRegistry(str(runtime_policy_path))


def test_policy_registry_raises_for_mismatched_visa_family(tmp_path) -> None:
    policy_pack_directory = tmp_path / "policy_packs"
    policy_pack_directory.mkdir()
    (policy_pack_directory / "f1.yaml").write_text("visa_family: j1\n")

    with pytest.raises(ValueError, match="visa_family mismatch"):
        PolicyRegistry(str(policy_pack_directory))
