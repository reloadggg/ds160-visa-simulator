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
    assert "BASE QUESTION" not in instructions
