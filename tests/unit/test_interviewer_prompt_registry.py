from pathlib import Path

from app.services.interviewer_prompt_registry import InterviewerPromptRegistry


def test_prompt_registry_reads_base_prompt_sections(tmp_path: Path) -> None:
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

    registry = InterviewerPromptRegistry(prompt_dir=str(prompt_dir))

    instructions = registry.build_instructions("question_agent")

    assert "BASE ROLE" in instructions
    assert "BASE STYLE" in instructions
    assert "BASE RULES" in instructions
    assert "BASE OUTPUT" in instructions
    assert "BASE CASE SLOT" in instructions
    assert "BASE QUESTION" in instructions


def test_prompt_registry_applies_family_override(tmp_path: Path) -> None:
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
                "sections:",
                "  judgment_rules: |",
                "    F1 RULES",
                "modules:",
                "  question_agent: |",
                "    F1 QUESTION",
            ]
        ),
        encoding="utf-8",
    )

    registry = InterviewerPromptRegistry(prompt_dir=str(prompt_dir))

    instructions = registry.build_instructions("question_agent", declared_family="f1")

    assert "BASE ROLE" in instructions
    assert "BASE STYLE" in instructions
    assert "F1 RULES" in instructions
    assert "BASE OUTPUT" in instructions
    assert "BASE CASE SLOT" in instructions
    assert "F1 QUESTION" in instructions
    assert "BASE QUESTION" in instructions


def test_f1_prompt_instructs_refusal_when_required_funding_proof_unavailable() -> None:
    instructions = InterviewerPromptRegistry().build_instructions(
        "adjudication_agent",
        declared_family="f1",
    )

    assert "document_review" in instructions
    assert "high_risk" in instructions
    assert "只进入复核" in instructions
    assert "simulated_refusal" in instructions
    assert "I-20 第一年度费用无法由已提供资金覆盖" in instructions


def test_f1_adjudication_prompt_keeps_base_contract_and_family_addendum() -> None:
    instructions = InterviewerPromptRegistry().build_instructions(
        "adjudication_agent",
        declared_family="f1",
    )

    assert "你必须综合 dynamic_turn_context、tool_outputs、当前用户消息一起判断" in instructions
    assert "requested_documents 最多 1 个" in instructions
    assert "document_review 是内部审阅意见，不是对用户话术" in instructions
    assert "当前是 F-1 面谈语境" in instructions
    assert "毕业后你打算回国做什么工作？" in instructions
    assert "我听到了" in instructions
    assert "具体一点" in instructions
