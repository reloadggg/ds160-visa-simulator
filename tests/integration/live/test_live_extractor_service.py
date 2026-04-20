import pytest

from app.agents.model_factory import AgentModelFactory
from app.agents.extractor_agent import ExtractorAgentRunner
from app.domain.contracts import ApplicantProfile
from app.services.extractor_service import ExtractorService


@pytest.mark.live_llm
def test_live_extractor_maps_parental_funding_via_agent_runtime(
    live_db_session_factory,
    live_expected_runtime_model,
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-live-extractor-1")
    build_calls: list[tuple[str, str, str | None]] = []
    run_calls: list[str] = []
    original_build = AgentModelFactory.build
    original_run = ExtractorAgentRunner.run

    def tracked_build(self, module_key, stage_key):
        model, runtime = original_build(self, module_key, stage_key)
        build_calls.append((module_key, stage_key, runtime.get("model")))
        return model, runtime

    def tracked_run(self, *, deps, message_text, profile_payload):
        run_calls.append(deps.session_id)
        return original_run(
            self,
            deps=deps,
            message_text=message_text,
            profile_payload=profile_payload,
        )

    monkeypatch.setattr(AgentModelFactory, "build", tracked_build)
    monkeypatch.setattr(ExtractorAgentRunner, "run", tracked_run)
    with live_db_session_factory() as db:
        updated = ExtractorService(db=db).apply_message(
            profile,
            "My mother and father will cover all my tuition and living expenses.",
        )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"
    assert updated.field_provenance["/funding/primary_source"].evidence_refs == []
    assert build_calls == [
        (
            "extractor_agent",
            "interview_turn",
            live_expected_runtime_model("extractor_agent", "interview_turn"),
        )
    ]
    assert run_calls
    assert run_calls[-1] == "live-extractor-1"


@pytest.mark.live_llm
def test_live_extractor_keeps_unknown_when_funding_not_decided(
    live_db_session_factory,
    live_expected_runtime_model,
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-live-extractor-2")
    build_calls: list[tuple[str, str, str | None]] = []
    run_calls: list[str] = []
    original_build = AgentModelFactory.build
    original_run = ExtractorAgentRunner.run

    def tracked_build(self, module_key, stage_key):
        model, runtime = original_build(self, module_key, stage_key)
        build_calls.append((module_key, stage_key, runtime.get("model")))
        return model, runtime

    def tracked_run(self, *, deps, message_text, profile_payload):
        run_calls.append(deps.session_id)
        return original_run(
            self,
            deps=deps,
            message_text=message_text,
            profile_payload=profile_payload,
        )

    monkeypatch.setattr(AgentModelFactory, "build", tracked_build)
    monkeypatch.setattr(ExtractorAgentRunner, "run", tracked_run)
    with live_db_session_factory() as db:
        updated = ExtractorService(db=db).apply_message(
            profile,
            "I have not decided who will pay yet.",
        )

    assert updated.field_states["/funding/primary_source"].state.value == "unknown"
    assert "primary_source" not in updated.funding
    assert build_calls == [
        (
            "extractor_agent",
            "interview_turn",
            live_expected_runtime_model("extractor_agent", "interview_turn"),
        )
    ]
    assert run_calls
    assert run_calls[-1] == "live-extractor-2"
