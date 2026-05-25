from __future__ import annotations

from app.domain.agent_runtime import GraphRunResult
from app.services.llm_node_runner import LLMNodeRequest, StubLLMNodeRunner


def test_stub_llm_node_runner_records_request_without_real_model() -> None:
    output = GraphRunResult(
        assistant_message="你为什么选择这个项目？",
        decision="continue_interview",
    )
    runner = StubLLMNodeRunner(output, metadata={"case": "unit"})

    response = runner.run(
        LLMNodeRequest(
            node_name="adjudication",
            prompt="{}",
            instructions="Return GraphRunResult.",
            output_type=GraphRunResult,
            model=None,
            runtime={"provider": "test", "model": "stub"},
        )
    )

    assert response.output == output
    assert response.metadata["runner"] == "stub"
    assert response.metadata["case"] == "unit"
    assert runner.requests[0].node_name == "adjudication"
