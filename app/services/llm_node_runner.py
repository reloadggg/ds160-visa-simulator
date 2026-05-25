from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class LLMNodeRequest:
    node_name: str
    prompt: str
    instructions: str
    output_type: type[OutputT]
    model: Any
    runtime: dict[str, Any]


@dataclass(frozen=True)
class LLMNodeResponse:
    output: BaseModel
    metadata: dict[str, Any]


class LLMNodeRunner(Protocol):
    def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
        """Run one typed LLM node without owning graph state or product memory."""


class PydanticAILLMNodeRunner:
    """Default typed model-call adapter used behind LangGraph nodes."""

    def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
        agent = Agent(
            request.model,
            output_type=request.output_type,
            instructions=request.instructions,
        )
        result = agent.run_sync(request.prompt)
        return LLMNodeResponse(
            output=result.output,
            metadata={
                "node_name": request.node_name,
                "runner": "pydantic_ai",
                "provider": request.runtime.get("provider"),
                "model": request.runtime.get("model"),
            },
        )


class StubLLMNodeRunner:
    """Test helper for graph nodes; keeps contract tests free of real models."""

    def __init__(self, output: BaseModel, metadata: dict[str, Any] | None = None) -> None:
        self.output = output
        self.metadata = dict(metadata or {})
        self.requests: list[LLMNodeRequest] = []

    def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
        self.requests.append(request)
        return LLMNodeResponse(
            output=self.output,
            metadata={
                "node_name": request.node_name,
                "runner": "stub",
                **self.metadata,
            },
        )
