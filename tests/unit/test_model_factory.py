from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.models.openai import OpenAIChatModel

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner
from app.agents.schemas import (
    ConsistencyFinding,
    InterviewNextAction,
    RiskFlagProposal,
    ScoreProposal,
)


def test_model_factory_returns_none_without_openai_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    model, runtime = AgentModelFactory().build("scoring_agent", "interview_turn")

    assert model is None
    assert runtime["model"] == "gpt-5.4"
    assert runtime["reasoning_effort"] == "xhigh"
    assert runtime["prompt_template_id"] == "scoring-agent-v1"
    # pydantic 2.12 不是漂移；当前 foundation 依赖 pydantic-ai-slim 1.77 的安装约束。
    assert runtime["prompt_version"] == "v1"


def test_model_factory_returns_empty_runtime_for_missing_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    model, runtime = AgentModelFactory().build("missing_agent", "missing_stage")

    assert model is None
    assert runtime == {
        "provider": None,
        "model": None,
        "reasoning_effort": None,
        "prompt_template_id": None,
        "prompt_version": None,
    }


def test_model_factory_builds_openai_chat_model_from_custom_runtime_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_policy_path = tmp_path / "runtime.yaml"
    runtime_policy_path.write_text(
        "\n".join(
            [
                "scoring_agent:",
                "  interview_turn:",
                "    provider: openai_compatible",
                "    model: gpt-5.4",
                "    reasoning_effort: xhigh",
                "    prompt_template_id: scoring-agent-v1",
                "    prompt_version: v1",
            ]
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model, runtime = AgentModelFactory(
        runtime_policy_path=str(runtime_policy_path)
    ).build("scoring_agent", "interview_turn")

    assert isinstance(model, OpenAIChatModel)
    assert runtime["provider"] == "openai_compatible"
    assert runtime["model"] == "gpt-5.4"


def test_model_factory_reads_env_overrides_from_runtime_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_MODEL", "gpt-5.4")
    monkeypatch.setenv(
        "RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_REASONING_EFFORT",
        "xhigh",
    )

    model, runtime = AgentModelFactory().build("question_agent", "interview_turn")

    assert model is None
    assert runtime["model"] == "gpt-5.4"
    assert runtime["reasoning_effort"] == "xhigh"


def test_model_factory_returns_none_for_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RUNTIME_DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv(
        "RUNTIME_QUESTION_AGENT_INTERVIEW_TURN_PROVIDER",
        raising=False,
    )
    runtime_policy_path = tmp_path / "runtime.yaml"
    runtime_policy_path.write_text(
        "\n".join(
            [
                "question_agent:",
                "  interview_turn:",
                "    provider: unsupported_vendor",
                "    model: gpt-5.4",
                "    reasoning_effort: high",
                "    prompt_template_id: question-agent-v1",
                "    prompt_version: v1",
            ]
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    model, runtime = AgentModelFactory(
        runtime_policy_path=str(runtime_policy_path)
    ).build("question_agent", "interview_turn")

    assert model is None
    assert runtime["provider"] == "unsupported_vendor"


def test_model_factory_builds_prompt_instructions_from_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_policy_path = tmp_path / "runtime.yaml"
    runtime_policy_path.write_text(
        "\n".join(
            [
                "question_agent:",
                "  interview_turn:",
                "    provider: openai_compatible",
                "    model: gpt-5.4",
                "    reasoning_effort: high",
                "    prompt_template_id: question-agent-v1",
                "    prompt_version: v1",
            ]
        ),
        encoding="utf-8",
    )
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "base.yaml").write_text(
        "\n".join(
            [
                "sections:",
                "  role: |",
                "    BASE ROLE",
                "  interview_style: |",
                "    BASE STYLE",
                "  judgment_rules: |",
                "    BASE RULES",
                "  output_rules: |",
                "    BASE OUTPUT",
                "  future_case_slot: |",
                "    BASE CASE SLOT",
                "modules:",
                "  question_agent: |",
                "    BASE QUESTION",
            ]
        ),
        encoding="utf-8",
    )
    (prompt_dir / "f1.yaml").write_text(
        "\n".join(
            [
                "modules:",
                "  question_agent: |",
                "    F1 QUESTION",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    _model, runtime = AgentModelFactory(
        runtime_policy_path=str(runtime_policy_path),
        prompt_dir=str(prompt_dir),
    ).build("question_agent", "interview_turn", declared_family="f1")

    assert "BASE ROLE" in runtime["instructions"]
    assert "F1 QUESTION" in runtime["instructions"]
    assert "BASE QUESTION" not in runtime["instructions"]


def test_question_agent_runner_uses_registry_instructions_instead_of_hardcoded_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class DummyAgent:
        def __init__(
            self,
            model,
            *,
            deps_type,
            output_type,
            instructions,
        ) -> None:
            captured["instructions"] = instructions

    monkeypatch.setattr("app.agents.question_agent.Agent", DummyAgent)
    monkeypatch.setattr(
        "app.agents.question_agent.register_evidence_tools",
        lambda agent: None,
    )

    QuestionAgentRunner(model=object(), instructions="CONFIGURED QUESTION PROMPT")

    assert captured["instructions"] == "CONFIGURED QUESTION PROMPT"


def test_score_proposal_requires_refs_for_confirmed_high_risk() -> None:
    with pytest.raises(ValueError):
        ScoreProposal(
            category_fit=70,
            document_readiness=20,
            narrative_consistency=10,
            confidence=80,
            risk_flags=[
                RiskFlagProposal(
                    code="hard_conflict",
                    severity="high",
                    status="confirmed",
                    summary="self-reported fraud",
                    evidence_refs=[],
                )
            ],
        )


def test_score_proposal_keeps_requested_documents() -> None:
    proposal = ScoreProposal(
        category_fit=70,
        document_readiness=20,
        narrative_consistency=10,
        confidence=80,
        requested_documents=["bank_statement", "funding_letter"],
    )

    assert proposal.requested_documents == ["bank_statement", "funding_letter"]


def test_schema_control_fields_reject_unknown_values() -> None:
    with pytest.raises(ValidationError):
        ConsistencyFinding(
            finding_type="typo_gap",
            severity="urgent",
            status="pending",
            summary="invalid finding control fields",
            evidence_refs=[],
        )

    with pytest.raises(ValidationError):
        InterviewNextAction(
            assistant_message="Please upload your funding proof.",
            requested_documents=["funding_proof"],
            decision_hint="ask_more_questions",
        )


def test_interview_next_action_rejects_multiple_requested_documents() -> None:
    with pytest.raises(ValidationError):
        InterviewNextAction(
            assistant_message="Please upload the requested evidence.",
            requested_documents=["passport_bio", "funding_proof"],
            decision_hint="need_more_evidence",
        )


def test_interview_next_action_rejects_checklist_style_summary() -> None:
    with pytest.raises(ValidationError):
        InterviewNextAction(
            assistant_message="Summary:\n- Explain your study plan.\n- Upload funding proof.",
            requested_documents=[],
            decision_hint="continue_interview",
        )


def test_interview_next_action_rejects_question_plus_document_combo() -> None:
    with pytest.raises(ValidationError):
        InterviewNextAction(
            assistant_message="Why did you choose this school?",
            requested_documents=["funding_proof"],
            decision_hint="need_more_evidence",
        )
