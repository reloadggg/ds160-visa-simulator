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
    assert "真实窗口节奏是“核验式短问”" in instructions
    assert "为什么不在国内读？" in instructions
    assert "第一年的费用由谁支付？" in instructions
    assert "不要像面试教练评价答案" in instructions
    assert "这个回答太笼统" in instructions


def test_j1_prompt_focuses_exchange_program_not_f1_school_choice() -> None:
    instructions = InterviewerPromptRegistry().build_instructions(
        "adjudication_agent",
        declared_family="j1",
    )
    question_instructions = InterviewerPromptRegistry().build_instructions(
        "question_agent",
        declared_family="j1",
    )

    assert "当前是 J-1 面谈语境" in instructions
    assert "DS-2019" in instructions
    assert "sponsor" in instructions
    assert "项目结束后你回国继续做什么？" in instructions
    assert "不要套用 F-1 选校/选专业话术" in question_instructions


def test_b1_b2_prompt_focuses_temporary_visit_and_not_study_or_work() -> None:
    instructions = InterviewerPromptRegistry().build_instructions(
        "adjudication_agent",
        declared_family="b1_b2",
    )
    question_instructions = InterviewerPromptRegistry().build_instructions(
        "question_agent",
        declared_family="b1_b2",
    )

    assert "当前是 B-1/B-2 面谈语境" in instructions
    assert "临时访问目的" in instructions
    assert "你计划停留多久？" in instructions
    assert "这次费用由谁支付？" in instructions
    assert "不要问成 F-1 学习计划" in question_instructions
    assert "为什么选择这个学校" not in instructions


def test_h1b_prompt_focuses_petition_and_avoids_technical_interview() -> None:
    instructions = InterviewerPromptRegistry().build_instructions(
        "adjudication_agent",
        declared_family="h1b",
    )
    question_instructions = InterviewerPromptRegistry().build_instructions(
        "question_agent",
        declared_family="h1b",
    )

    assert "当前是 H-1B 面谈语境" in instructions
    assert "I-797" in instructions
    assert "薪资/LCA" in instructions
    assert "你的美国雇主是哪家公司？" in instructions
    assert "不要问成技术面试" in question_instructions
    assert "模型架构" in instructions


def test_default_fallback_messages_are_window_style() -> None:
    registry = InterviewerPromptRegistry()
    fallback = registry.fallback_messages()

    assert fallback["need_more_evidence"] == "请提供当前这项补强证据。"
    assert fallback["high_risk_review"] == "这里有关键不一致，请解释。"
    for decision in (
        "need_more_evidence",
        "route_correction",
        "high_risk_review",
        "simulated_refusal",
    ):
        message = fallback[decision]
        assert "当前案例" not in message
        assert "正式问答" not in message
        assert "关键点" not in message
