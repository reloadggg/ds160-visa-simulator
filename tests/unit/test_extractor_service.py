from pydantic_ai.models.test import TestModel

from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding
from app.domain.contracts import ApplicantProfile, FieldState
from app.services.consistency_service import ConsistencyService
from app.services.extractor_service import ExtractorService


def test_extractor_service_uses_agent_output_when_model_is_available(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-1")

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "field_updates": [
                        {
                            "field_path": "/funding/primary_source",
                            "value": "parents",
                            "state": "claimed",
                            "evidence_refs": ["msg:last_user_turn"],
                        }
                    ],
                    "required_evidence_queries": ["bank statement"],
                    "notes": [],
                },
            ),
            {"model": "gpt-5.4"},
        ),
    )
    monkeypatch.setattr(
        ExtractorService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )
    monkeypatch.setattr(
        ExtractorService,
        "_fallback_apply_message",
        lambda self, profile, message_text: (_ for _ in ()).throw(
            AssertionError("agent path should not fall back")
        ),
    )

    updated = ExtractorService(db=object()).apply_message(
        profile,
        "My parents will pay for my studies.",
    )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"
    assert updated.field_provenance["/funding/primary_source"].evidence_refs == []


def test_extractor_service_falls_back_without_model(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-2")

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (None, {"model": "gpt-5.4"}),
    )

    updated = ExtractorService().apply_message(
        profile,
        "My mother and father will cover all my tuition and living expenses.",
    )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"


def test_extractor_service_falls_back_when_agent_runtime_errors(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-3")

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (object(), {"model": "gpt-5.4"}),
    )
    monkeypatch.setattr(
        ExtractorService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )
    monkeypatch.setattr(
        "app.services.extractor_service.ExtractorAgentRunner.run",
        lambda self, *, deps, message_text, profile_payload: (_ for _ in ()).throw(
            RuntimeError("tool failure")
        ),
    )

    updated = ExtractorService(db=object()).apply_message(
        profile,
        "My parents will pay for my studies.",
    )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"


def test_consistency_service_returns_typed_gap_finding() -> None:
    profile = ApplicantProfile.minimal("profile-extractor-4")
    profile.funding["primary_source"] = "parents"

    findings = ConsistencyService().evaluate(profile)

    assert len(findings) == 1
    assert isinstance(findings[0], ConsistencyFinding)
    assert findings[0].finding_type == "gap"


def test_extractor_service_does_not_clear_known_funding_with_unknown_update(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-5")
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"].state = FieldState.CLAIMED

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "field_updates": [
                        {
                            "field_path": "/funding/primary_source",
                            "value": None,
                            "state": "unknown",
                            "evidence_refs": [],
                        }
                    ],
                    "required_evidence_queries": [],
                    "notes": [],
                },
            ),
            {"model": "gpt-5.4"},
        ),
    )
    monkeypatch.setattr(
        ExtractorService,
        "_build_agent_deps",
        lambda self, profile: AgentRuntimeDeps(
            session_id=profile.profile_id,
            retrieval=object(),
            evidence=object(),
        ),
    )

    updated = ExtractorService(db=object()).apply_message(
        profile,
        "I will study computer science.",
    )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"
