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


def test_extractor_service_applies_non_funding_field_updates_from_agent(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-3b")

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "field_updates": [
                        {
                            "field_path": "/education/school_name",
                            "value": "Stanford University",
                            "state": "claimed",
                            "evidence_refs": ["msg:last_user_turn"],
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
        "I will attend Stanford University.",
    )

    assert updated.education["school_name"] == "Stanford University"
    assert updated.field_states["/education/school_name"].state.value == "claimed"
    assert updated.field_provenance["/education/school_name"].evidence_refs == []
    assert updated.ds160_view["field_claim_history"]["/education/school_name"] == [
        {
            "value": "Stanford University",
            "content": "I will attend Stanford University.",
            "turn_id": None,
            "turn_index": 1,
            "source": "user_message",
        }
    ]


def test_non_funding_claimed_value_flows_from_extractor_into_consistency_check(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-3c")
    profile.ds160_view["document_evidence_snapshot"] = {
        "/education/school_name": {
            "value": "Stanford University",
            "state": "documented",
            "evidence_refs": ["evi-school"],
        }
    }

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "field_updates": [
                        {
                            "field_path": "/education/school_name",
                            "value": "University of California, Berkeley",
                            "state": "claimed",
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
        "I will attend UC Berkeley.",
    )
    findings = ConsistencyService().evaluate(updated)

    school_conflict = next(
        finding for finding in findings if finding.finding_type == "record_conflict"
    )
    assert school_conflict.summary == (
        "oral explanation conflicts with documented evidence for /education/school_name"
    )
    assert school_conflict.evidence_refs == ["evi-school", "msg:turn_index:1"]


def test_consistency_service_returns_typed_gap_finding() -> None:
    profile = ApplicantProfile.minimal("profile-extractor-4")
    profile.funding["primary_source"] = "parents"

    findings = ConsistencyService().evaluate(profile)

    assert len(findings) == 1
    assert isinstance(findings[0], ConsistencyFinding)
    assert findings[0].finding_type == "gap"


def test_consistency_service_uses_matching_turn_id_for_hard_conflict() -> None:
    profile = ApplicantProfile.minimal("profile-extractor-4b")
    profile.ds160_view["turn_history"] = [
        {
            "turn_id": "turn-1",
            "turn_index": 1,
            "role": "user",
            "content": "I want to study computer science.",
            "source": "user_message",
        },
        {
            "turn_id": "turn-2",
            "turn_index": 2,
            "role": "assistant",
            "content": "How will you pay for school?",
            "source": "interviewer_runtime_service",
        },
        {
            "turn_id": "turn-3",
            "turn_index": 3,
            "role": "user",
            "content": "I lied about my supporting documents.",
            "source": "user_message",
        },
        {
            "turn_id": "turn-4",
            "turn_index": 4,
            "role": "user",
            "content": "I want to explain my school plan now.",
            "source": "user_message",
        },
    ]

    findings = ConsistencyService().evaluate(profile)

    assert len(findings) == 1
    assert findings[0].finding_type == "hard_conflict"
    assert findings[0].evidence_refs == ["msg:turn-3"]


def test_consistency_service_falls_back_to_last_user_message_without_turn_history() -> None:
    profile = ApplicantProfile.minimal("profile-extractor-4c")
    profile.ds160_view["last_user_message"] = "I lied about my supporting documents."

    findings = ConsistencyService().evaluate(profile)

    assert len(findings) == 1
    assert findings[0].finding_type == "hard_conflict"
    assert findings[0].evidence_refs == ["msg:last_user_turn"]


def test_consistency_service_parses_correction_style_funding_claims() -> None:
    service = ConsistencyService()

    assert (
        service._parse_funding_claim_value(
            "Actually not my parents. I will pay for my education."
        )
        == "self"
    )
    assert (
        service._parse_funding_claim_value(
            "Actually not my parents, I will cover it with my savings."
        )
        == "self"
    )
    assert (
        service._parse_funding_claim_value(
            "My parents are not paying for my tuition."
        )
        is None
    )
    assert (
        service._parse_funding_claim_value(
            "My father is not sponsoring me."
        )
        is None
    )


def test_consistency_service_supports_non_funding_document_backed_conflicts() -> None:
    profile = ApplicantProfile.minimal("profile-extractor-4d")
    profile.ds160_view["field_claim_history"] = {
        "/education/school_name": [
            {
                "value": "Massachusetts Institute of Technology",
                "content": "I will attend MIT.",
                "turn_id": "turn-1",
                "turn_index": 1,
                "source": "user_message",
            }
        ]
    }
    profile.ds160_view["document_evidence_snapshot"] = {
        "/education/school_name": {
            "value": "Stanford University",
            "state": "documented",
            "evidence_refs": ["evi-school"],
        }
    }

    findings = ConsistencyService().evaluate(profile)

    school_conflict = next(
        finding for finding in findings if finding.finding_type == "record_conflict"
    )
    assert school_conflict.summary == (
        "oral explanation conflicts with documented evidence for /education/school_name"
    )
    assert school_conflict.evidence_refs == ["evi-school", "msg:turn-1"]


def test_consistency_service_deduplicates_legacy_claim_history_without_turn_metadata() -> None:
    profile = ApplicantProfile.minimal("profile-extractor-4e")
    profile.ds160_view["field_claim_history"] = {
        "/funding/primary_source": [
            {
                "value": "parents",
                "content": "parents",
                "turn_id": None,
                "turn_index": None,
                "source": "user_claim",
            }
        ]
    }
    profile.ds160_view["turn_history"] = [
        {
            "turn_id": "turn-1",
            "turn_index": 1,
            "role": "user",
            "content": "My parents will pay for my studies.",
            "source": "user_message",
        }
    ]
    profile.ds160_view["document_evidence_snapshot"] = {
        "/funding/primary_source": {
            "value": "self",
            "state": "documented",
            "evidence_refs": ["evi-funding"],
        }
    }

    findings = ConsistencyService().evaluate(profile)

    funding_conflict = next(
        finding for finding in findings if finding.finding_type == "record_conflict"
    )
    assert funding_conflict.severity == "medium"
    assert [item["value"] for item in profile.ds160_view["field_claim_history"]["/funding/primary_source"]] == [
        "parents"
    ]


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


def test_extractor_service_normalizes_undecided_funding_claim_to_unknown(
    monkeypatch,
) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-6")

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "field_updates": [
                        {
                            "field_path": "/funding/primary_source",
                            "value": "Undecided",
                            "state": "claimed",
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
        "I have not decided who will pay yet.",
    )

    assert "primary_source" not in updated.funding
    assert updated.field_states["/funding/primary_source"].state.value == "unknown"
    assert updated.field_provenance["/funding/primary_source"].evidence_refs == []
