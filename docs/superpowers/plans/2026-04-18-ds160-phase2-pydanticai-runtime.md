# DS-160 Phase 2 PydanticAI Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以替换式方案把当前手写 extractor/scoring/question 主路径切到 `PydanticAI + evidence tools`，同时保留现有 Governor、OpenAI-compatible 配置和 Phase 1 证据底座。

**Architecture:** 保持现有 `FastAPI + SQLAlchemy + SQLite` 单体，不引入 UI、durable execution 或 gate state 表。Phase 2 新增独立的 `PydanticAI` model factory、typed agent schema、evidence tool 层和三类 agent wrapper，并把它们挂到现有 `ExtractorService / ScoringService / MessageService` 之下，完成主路径替换但不重写 API 契约。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, SQLite, Pydantic 2.11, `pydantic-ai-slim[openai]` 1.77.x, pytest, httpx

---

## Scope Decomposition

这份计划只覆盖你已经确认的 **Phase 2 最小后端版**：

1. 引入 `PydanticAI` runtime 与 OpenAI-compatible provider factory
2. 建立 evidence retrieval / excerpt / document field extraction 工具层
3. 把 scoring 改造成 tool-based scoring
4. 把 extractor 改造成 tool-based extraction
5. 把 question generation / message orchestration 切到新 runtime
6. 补齐非 live 与 live 回归

明确不在本计划内：

- `Chainlit`
- `pydantic-graph` / `Temporal`
- `gate_review` 状态表
- `runtime_trace / score_history / governor_history` 持久化
- 完整 report trace 增强

## Official References

实现本计划时，优先查看以下官方文档：

- 安装：<https://pydantic.dev/docs/ai/overview/install/>
- OpenAI / OpenAI-compatible provider：<https://pydantic.dev/docs/ai/models/openai/>
- Function tools：<https://ai.pydantic.dev/tools/>
- Testing / `TestModel`：<https://ai.pydantic.dev/testing/>

## File Map

### Create

- `app/agents/schemas.py`
  - Phase 2 强类型输出、evidence tool 返回类型、typed findings
- `app/agents/model_factory.py`
  - 兼容 `OPENAI_BASE_URL / OPENAI_API_KEY` 的 `PydanticAI` model/provider factory
- `app/agents/tools.py`
  - 暴露给 agent 的 evidence function tools
- `app/agents/scoring_agent.py`
  - tool-based scoring wrapper
- `app/agents/extractor_agent.py`
  - tool-based extraction wrapper
- `app/agents/question_agent.py`
  - next-question generation wrapper
- `app/services/retrieval_service.py`
  - session 内 lexical evidence search
- `app/services/evidence_service.py`
  - excerpt lookup 与 document field extraction
- `tests/unit/test_model_factory.py`
  - factory 与 schema 基础约束
- `tests/unit/test_retrieval_service.py`
  - retrieval 行为
- `tests/unit/test_evidence_service.py`
  - excerpt / field extraction 行为
- `tests/unit/test_agent_tools.py`
  - tool registration 与 `TestModel` 行为
- `tests/unit/test_scoring_service.py`
  - scoring service 替换路径与 fallback
- `tests/unit/test_extractor_service.py`
  - extractor service 替换路径与 fallback
- `tests/integration/test_tool_based_scoring.py`
  - evidence 存在 / 缺失时的 scoring 行为

### Modify

- `pyproject.toml`
  - 增加 `pydantic-ai-slim[openai]`
- `app/runtime_policies/default.yaml`
  - 新增 extractor/scoring/question agent runtime 配置
- `app/services/consistency_service.py`
  - 输出改为 `ConsistencyFinding`
- `app/services/extractor_service.py`
  - 调用 `ExtractorAgentRunner`
- `app/services/scoring_service.py`
  - 调用 `ScoringAgentRunner`
- `app/services/message_service.py`
  - 调用 `QuestionAgentRunner`
- `tests/integration/live/test_live_extractor_service.py`
  - live extractor 走新 runtime
- `tests/integration/live/test_live_scoring_service.py`
  - live scoring 走新 runtime
- `tests/integration/live/test_live_messages_api.py`
  - live message flow 走新 runtime
- `tests/integration/live/test_live_llm_client.py`
  - 改成 live provider/factory 兼容测试

### Intentionally Unchanged

- `app/services/report_service.py`
  - 当前 report 仍可从 `profile_json + governor_decision` 推导，不在本阶段扩 trace
- `app/integrations/llm_client.py`
  - 旧抽象不再作为主路径，但暂不删除，避免把 Phase 2 变成大清理

## Phase 2 Acceptance Criteria

- `PydanticAI` 成为 extractor/scoring/question 的主运行时
- runtime 继续兼容现有 `OPENAI_BASE_URL / OPENAI_API_KEY`
- evidence tools 可用：
  - `search_evidence`
  - `get_evidence_excerpt`
  - `extract_document_fields`
- `ScoringService` 在有文档证据时不再把 `funding_proof` 误判为缺失
- `GovernorService` 仍是最终 refusal/continue 裁决层
- 非 live 回归继续通过
- live 回归至少覆盖：
  - extractor
  - scoring
  - messages/message flow

## Task 1: 引入 PydanticAI 依赖、强类型 schema 与 model factory

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/runtime_policies/default.yaml`
- Create: `app/agents/schemas.py`
- Create: `app/agents/model_factory.py`
- Create: `tests/unit/test_model_factory.py`

- [ ] **Step 1: 先写失败测试，锁定 schema 约束与 provider factory 的最小行为**

```python
# tests/unit/test_model_factory.py
import os

import pytest

from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import RiskFlagProposal, ScoreProposal


def test_model_factory_returns_none_without_openai_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    model, runtime = AgentModelFactory().build("scoring_agent", "interview_turn")

    assert model is None
    assert runtime["model"] == "gpt-5.4"


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
```

- [ ] **Step 2: 运行测试，确认当前仓库还没有 Phase 2 foundation**

Run: `uv run pytest tests/unit/test_model_factory.py -q`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents.model_factory'`

- [ ] **Step 3: 增加依赖、runtime policy、typed schema 与 OpenAI-compatible model factory**

```toml
# pyproject.toml
[project]
dependencies = [
  "fastapi>=0.116.0,<0.117.0",
  "uvicorn>=0.35.0,<0.36.0",
  "pydantic>=2.11.0,<2.12.0",
  "pydantic-settings>=2.10.0,<2.11.0",
  "sqlalchemy>=2.0.41,<2.1.0",
  "python-multipart>=0.0.20,<0.0.21",
  "pyyaml>=6.0.2,<6.1.0",
  "pymupdf>=1.26.0,<1.27.0",
  "python-docx>=1.2.0,<1.3.0",
  "pillow>=11.3.0,<11.4.0",
  "pytesseract>=0.3.13,<0.4.0",
  "httpx>=0.28.1,<0.29.0",
  "pydantic-ai-slim[openai]>=1.77.0,<1.78.0",
]
```

```yaml
# app/runtime_policies/default.yaml
scoring_engine:
  interview_turn:
    provider: openai
    model: gpt-5.4
    reasoning_effort: xhigh
    prompt_template_id: scoring-default
    prompt_version: v1
extractor_service:
  gate_review:
    provider: openai
    model: gpt-5.4
    reasoning_effort: xhigh
    prompt_template_id: extractor-default
    prompt_version: v1
scoring_agent:
  interview_turn:
    provider: openai_compatible
    model: gpt-5.4
    reasoning_effort: xhigh
    prompt_template_id: scoring-agent-v1
    prompt_version: v1
extractor_agent:
  interview_turn:
    provider: openai_compatible
    model: gpt-5.4
    reasoning_effort: xhigh
    prompt_template_id: extractor-agent-v1
    prompt_version: v1
question_agent:
  interview_turn:
    provider: openai_compatible
    model: gpt-5.4
    reasoning_effort: high
    prompt_template_id: question-agent-v1
    prompt_version: v1
```

```python
# app/agents/schemas.py
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from app.domain.contracts import FieldState
from app.domain.evidence import DocumentSourceType


class EvidenceHit(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    evidence_type: str
    field_path: str
    excerpt: str
    filename: str
    source_type: DocumentSourceType
    score: float = Field(ge=0.0)


class EvidenceExcerpt(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    filename: str
    source_type: DocumentSourceType
    excerpt: str


class FieldUpdate(BaseModel):
    field_path: str
    value: str | None = None
    state: FieldState
    evidence_refs: list[str] = Field(default_factory=list)


class ExtractorOutput(BaseModel):
    field_updates: list[FieldUpdate] = Field(default_factory=list)
    required_evidence_queries: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ConsistencyFinding(BaseModel):
    finding_type: str
    severity: str
    status: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)


class RiskFlagProposal(BaseModel):
    code: str
    severity: str
    status: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)


class ScoreProposal(BaseModel):
    category_fit: int = Field(ge=0, le=100)
    document_readiness: int = Field(ge=0, le=100)
    narrative_consistency: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    risk_flags: list[RiskFlagProposal] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    requested_documents: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_confirmed_high_risk_refs(self) -> "ScoreProposal":
        for flag in self.risk_flags:
            if flag.severity == "high" and flag.status == "confirmed" and not flag.evidence_refs:
                raise ValueError("confirmed high-risk flags require evidence_refs")
        return self


class InterviewNextAction(BaseModel):
    assistant_message: str
    requested_documents: list[str] = Field(default_factory=list)
    decision_hint: str


@dataclass
class AgentRuntimeDeps:
    session_id: str
    retrieval: object
    evidence: object
```

```python
# app/agents/model_factory.py
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.settings import settings
from app.services.runtime_policies import RuntimePolicyRegistry


class AgentModelFactory:
    def __init__(self, runtime_policy_path: str | None = None) -> None:
        self.registry = RuntimePolicyRegistry(runtime_policy_path or "app/runtime_policies/default.yaml")

    def build(self, module_key: str, stage_key: str):
        runtime = self.registry.get(module_key, stage_key)
        if not settings.openai_api_key or not settings.openai_base_url:
            return None, runtime

        provider = OpenAIProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
        model = OpenAIChatModel(runtime["model"], provider=provider)
        return model, runtime
```

- [ ] **Step 4: 安装依赖并运行 foundation 测试**

Run: `uv sync --dev && uv run pytest tests/unit/test_model_factory.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 runtime foundation**

```bash
git add pyproject.toml app/runtime_policies/default.yaml app/agents/schemas.py app/agents/model_factory.py tests/unit/test_model_factory.py
git commit -m "feat: add pydanticai runtime foundation"
```

## Task 2: 建立 retrieval / evidence service 与 agent tools

**Files:**
- Create: `app/services/retrieval_service.py`
- Create: `app/services/evidence_service.py`
- Create: `app/agents/tools.py`
- Create: `tests/unit/test_retrieval_service.py`
- Create: `tests/unit/test_evidence_service.py`
- Create: `tests/unit/test_agent_tools.py`

- [ ] **Step 1: 写失败测试，锁定 retrieval、excerpt lookup 和 tool 注册**

```python
# tests/unit/test_retrieval_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.services.retrieval_service import RetrievalService


def test_search_session_evidence_returns_ranked_hits(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retrieval.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with SessionLocal() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(DocumentRecord(document_id="doc-1", session_id="sess-1", filename="funding.txt"))
            db.add(
                DocumentChunkRecord(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    session_id="sess-1",
                    ordinal=0,
                    page_number=None,
                    text="Parent sponsor bank statement for tuition",
                    metadata_json={},
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id="sess-1",
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="parents",
                    excerpt="Parent sponsor bank statement",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with SessionLocal() as db:
            hits = RetrievalService(db).search_session_evidence(
                session_id="sess-1",
                query="parent bank statement",
            )

            assert hits[0].evidence_id == "evi-1"
            assert hits[0].field_path == "/funding/primary_source"
            assert hits[0].score > 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
```

```python
# tests/unit/test_evidence_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.services.evidence_service import EvidenceService


def test_extract_document_fields_returns_minimal_funding_schema(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'evidence-service.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with SessionLocal() as db:
            db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
            db.add(DocumentRecord(document_id="doc-1", session_id="sess-1", filename="funding.txt"))
            db.add(
                DocumentChunkRecord(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    session_id="sess-1",
                    ordinal=0,
                    page_number=None,
                    text="Parent sponsor bank statement for tuition",
                    metadata_json={},
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id="sess-1",
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="parents",
                    excerpt="Parent sponsor bank statement",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with SessionLocal() as db:
            payload = EvidenceService(db).extract_document_fields("doc-1", "funding_proof")
            assert payload == {"primary_source": "parents"}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
```

```python
# tests/unit/test_agent_tools.py
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.agents.schemas import AgentRuntimeDeps, EvidenceHit
from app.agents.tools import register_evidence_tools


@dataclass
class DummyRetrieval:
    called: bool = False

    def search_session_evidence(self, session_id: str, query: str, **_: object) -> list[EvidenceHit]:
        self.called = True
        return []


@dataclass
class DummyEvidence:
    def get_evidence_excerpt(self, evidence_id: str):
        return None

    def extract_document_fields(self, document_id: str, schema_name: str):
        return {}


def test_registered_tools_are_visible_to_testmodel() -> None:
    retrieval = DummyRetrieval()
    deps = AgentRuntimeDeps(session_id="sess-1", retrieval=retrieval, evidence=DummyEvidence())
    agent = Agent("test", deps_type=AgentRuntimeDeps, output_type=str)
    register_evidence_tools(agent)

    result = agent.run_sync("search evidence", deps=deps, model=TestModel(call_tools="all"))

    assert "search_evidence" in result.output
    assert retrieval.called is True
```

- [ ] **Step 2: 运行测试，确认 retrieval / tools 尚不存在**

Run: `uv run pytest tests/unit/test_retrieval_service.py tests/unit/test_evidence_service.py tests/unit/test_agent_tools.py -q`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现 retrieval、evidence service 与 function tools**

```python
# app/services/retrieval_service.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import EvidenceHit
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord
from app.domain.evidence import DocumentSourceType


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search_session_evidence(
        self,
        session_id: str,
        query: str,
        *,
        evidence_type: str | None = None,
        field_path: str | None = None,
        limit: int = 5,
    ) -> list[EvidenceHit]:
        tokens = [token for token in query.lower().split() if token]
        statement = (
            select(EvidenceItemRecord, DocumentChunkRecord, DocumentRecord)
            .join(DocumentChunkRecord, DocumentChunkRecord.chunk_id == EvidenceItemRecord.chunk_id)
            .join(DocumentRecord, DocumentRecord.document_id == EvidenceItemRecord.document_id)
            .where(EvidenceItemRecord.session_id == session_id)
        )
        rows = self.db.execute(statement).all()
        hits: list[EvidenceHit] = []
        for evidence, chunk, document in rows:
            if evidence_type and evidence.evidence_type != evidence_type:
                continue
            if field_path and evidence.field_path != field_path:
                continue
            haystack = f"{document.filename} {chunk.text} {evidence.excerpt}".lower()
            score = float(sum(1 for token in tokens if token in haystack))
            if score <= 0:
                continue
            source_type = document.artifact_json.get("source_type", "unknown")
            hits.append(
                EvidenceHit(
                    evidence_id=evidence.evidence_id,
                    document_id=evidence.document_id,
                    chunk_id=evidence.chunk_id,
                    evidence_type=evidence.evidence_type,
                    field_path=evidence.field_path,
                    excerpt=evidence.excerpt,
                    filename=document.filename,
                    source_type=DocumentSourceType(source_type),
                    score=score,
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]
```

```python
# app/services/evidence_service.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import EvidenceExcerpt
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord
from app.domain.evidence import DocumentSourceType


class EvidenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_evidence_excerpt(self, evidence_id: str) -> EvidenceExcerpt | None:
        row = self.db.execute(
            select(EvidenceItemRecord, DocumentChunkRecord, DocumentRecord)
            .join(DocumentChunkRecord, DocumentChunkRecord.chunk_id == EvidenceItemRecord.chunk_id)
            .join(DocumentRecord, DocumentRecord.document_id == EvidenceItemRecord.document_id)
            .where(EvidenceItemRecord.evidence_id == evidence_id)
        ).one_or_none()
        if row is None:
            return None

        evidence, _chunk, document = row
        return EvidenceExcerpt(
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            chunk_id=evidence.chunk_id,
            filename=document.filename,
            source_type=DocumentSourceType(document.artifact_json.get("source_type", "unknown")),
            excerpt=evidence.excerpt,
        )

    def extract_document_fields(self, document_id: str, schema_name: str) -> dict[str, str]:
        if schema_name != "funding_proof":
            return {}

        rows = self.db.scalars(
            select(EvidenceItemRecord).where(EvidenceItemRecord.document_id == document_id)
        ).all()
        for row in rows:
            if row.field_path == "/funding/primary_source" and row.value == "parents":
                return {"primary_source": "parents"}
        return {}
```

```python
# app/agents/tools.py
from pydantic_ai import Agent, RunContext

from app.agents.schemas import AgentRuntimeDeps


def register_evidence_tools(agent: Agent[AgentRuntimeDeps, object]) -> None:
    @agent.tool
    def search_evidence(
        ctx: RunContext[AgentRuntimeDeps],
        query: str,
        evidence_type: str | None = None,
        field_path: str | None = None,
    ):
        """Search evidence items in the current session."""
        return ctx.deps.retrieval.search_session_evidence(
            session_id=ctx.deps.session_id,
            query=query,
            evidence_type=evidence_type,
            field_path=field_path,
        )

    @agent.tool
    def get_evidence_excerpt(
        ctx: RunContext[AgentRuntimeDeps],
        evidence_id: str,
    ):
        """Fetch the canonical excerpt for one evidence item."""
        return ctx.deps.evidence.get_evidence_excerpt(evidence_id)

    @agent.tool
    def extract_document_fields(
        ctx: RunContext[AgentRuntimeDeps],
        document_id: str,
        schema_name: str,
    ):
        """Extract a minimal structured payload from one document."""
        return ctx.deps.evidence.extract_document_fields(document_id, schema_name)
```

- [ ] **Step 4: 运行 unit tests，确认服务层和工具层都可用**

Run: `uv run pytest tests/unit/test_retrieval_service.py tests/unit/test_evidence_service.py tests/unit/test_agent_tools.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 retrieval / tools 基础设施**

```bash
git add app/services/retrieval_service.py app/services/evidence_service.py app/agents/tools.py tests/unit/test_retrieval_service.py tests/unit/test_evidence_service.py tests/unit/test_agent_tools.py
git commit -m "feat: add evidence retrieval tools"
```

## Task 3: 用 PydanticAI 替换 scoring 主路径

**Files:**
- Create: `app/agents/scoring_agent.py`
- Create: `tests/unit/test_scoring_service.py`
- Create: `tests/integration/test_tool_based_scoring.py`
- Modify: `app/services/scoring_service.py`
- Modify: `tests/integration/live/test_live_scoring_service.py`

- [ ] **Step 1: 写失败测试，锁定 tool-based scoring 与 fallback**

```python
# tests/unit/test_scoring_service.py
from pydantic_ai.models.test import TestModel

from app.agents.schemas import ScoreProposal
from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.services.scoring_service import ScoringService


def test_scoring_service_uses_agent_output_when_model_is_available(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(state=FieldState.DOCUMENTED)

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                custom_output_args={
                    "category_fit": 78,
                    "document_readiness": 90,
                    "narrative_consistency": 82,
                    "confidence": 75,
                    "risk_flags": [],
                    "missing_evidence": [],
                    "requested_documents": [],
                }
            ),
            {"model": "gpt-5.4"},
        ),
    )

    score = ScoringService().propose(profile, findings=[], scoring_stage="interview_turn")

    assert score.document_readiness == 90
    assert score.missing_evidence == []
```

```python
# tests/integration/test_tool_based_scoring.py
from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.services.consistency_service import ConsistencyService
from app.services.scoring_service import ScoringService


def test_tool_based_scoring_keeps_funding_gap_when_parent_claim_unproven(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-tool-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(state=FieldState.CLAIMED)
    findings = ConsistencyService().evaluate(profile)

    monkeypatch.setattr(
        "app.services.scoring_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (None, {"model": "gpt-5.4"}),
    )

    score = ScoringService().propose(profile, findings, scoring_stage="interview_turn")

    assert "funding_proof" in score.missing_evidence
```

- [ ] **Step 2: 运行测试，确认 scoring agent 尚未接通**

Run: `uv run pytest tests/unit/test_scoring_service.py tests/integration/test_tool_based_scoring.py -q`  
Expected: FAIL with import or attribute errors for the new agent path

- [ ] **Step 3: 新建 ScoringAgentRunner，并把 ScoringService 改成“agent 优先、无配置 fallback”**

```python
# app/agents/scoring_agent.py
from pydantic_ai import Agent

from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding, ScoreProposal
from app.agents.tools import register_evidence_tools


class ScoringAgentRunner:
    def __init__(self, model=None) -> None:
        resolved_model = model
        if resolved_model is None:
            resolved_model, _runtime = AgentModelFactory().build("scoring_agent", "interview_turn")

        self.agent = Agent(
            resolved_model,
            deps_type=AgentRuntimeDeps,
            output_type=ScoreProposal,
            instructions=(
                "Score one DS-160 interview turn. Missing evidence must stay unknown rather than false. "
                "Use tools before making any document-sensitive judgment. "
                "High confirmed risk flags must include evidence_refs."
            ),
        )
        register_evidence_tools(self.agent)

    def run(
        self,
        *,
        deps: AgentRuntimeDeps,
        profile_payload: dict,
        findings: list[ConsistencyFinding],
    ) -> ScoreProposal:
        prompt = {
            "profile": profile_payload,
            "findings": [item.model_dump(mode="json") for item in findings],
        }
        return self.agent.run_sync(str(prompt), deps=deps).output
```

```python
# app/services/scoring_service.py
from app.agents.model_factory import AgentModelFactory
from app.agents.scoring_agent import ScoringAgentRunner
from app.agents.schemas import AgentRuntimeDeps, ConsistencyFinding, ScoreProposal
from app.services.evidence_service import EvidenceService
from app.services.retrieval_service import RetrievalService


class ScoringService:
    def __init__(self, db=None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()

    def propose(self, profile, findings, scoring_stage: str):
        model, _runtime = self.model_factory.build("scoring_agent", scoring_stage)
        if model is None or self.db is None:
            return self._fallback_score(profile, findings)

        typed_findings = [
            item if isinstance(item, ConsistencyFinding) else ConsistencyFinding.model_validate(item)
            for item in findings
        ]
        deps = AgentRuntimeDeps(
            session_id=profile.profile_id.replace("profile-", ""),
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
        )
        proposal = ScoringAgentRunner(model=model).run(
            deps=deps,
            profile_payload=profile.model_dump(mode="json"),
            findings=typed_findings,
        )
        return self._proposal_to_score_state(profile, proposal, scoring_stage)
```

- [ ] **Step 4: 运行 scoring 相关单测与集成测试**

Run: `uv run pytest tests/unit/test_scoring_service.py tests/integration/test_tool_based_scoring.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 scoring runtime 替换**

```bash
git add app/agents/scoring_agent.py app/services/scoring_service.py tests/unit/test_scoring_service.py tests/integration/test_tool_based_scoring.py tests/integration/live/test_live_scoring_service.py
git commit -m "feat: replace scoring runtime with pydanticai"
```

## Task 4: 用 PydanticAI 替换 extractor 主路径，并把 findings 变成 typed models

**Files:**
- Create: `app/agents/extractor_agent.py`
- Create: `tests/unit/test_extractor_service.py`
- Modify: `app/services/extractor_service.py`
- Modify: `app/services/consistency_service.py`
- Modify: `tests/integration/live/test_live_extractor_service.py`

- [ ] **Step 1: 写失败测试，锁定 extractor agent path 与 typed findings**

```python
# tests/unit/test_extractor_service.py
from pydantic_ai.models.test import TestModel

from app.domain.contracts import ApplicantProfile
from app.services.extractor_service import ExtractorService


def test_extractor_service_uses_agent_output_when_model_is_available(monkeypatch) -> None:
    profile = ApplicantProfile.minimal("profile-extractor-1")

    monkeypatch.setattr(
        "app.services.extractor_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
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
                }
            ),
            {"model": "gpt-5.4"},
        ),
    )

    updated = ExtractorService().apply_message(profile, "My parents will pay for my studies.")

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"
```

- [ ] **Step 2: 运行测试，确认 extractor 还没有替换到新 runtime**

Run: `uv run pytest tests/unit/test_extractor_service.py -q`  
Expected: FAIL with import or attribute errors for the new agent path

- [ ] **Step 3: 实现 ExtractorAgentRunner，并把 ConsistencyService 改成 typed output**

```python
# app/agents/extractor_agent.py
from pydantic_ai import Agent

from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import AgentRuntimeDeps, ExtractorOutput
from app.agents.tools import register_evidence_tools


class ExtractorAgentRunner:
    def __init__(self, model=None) -> None:
        resolved_model = model
        if resolved_model is None:
            resolved_model, _runtime = AgentModelFactory().build("extractor_agent", "interview_turn")

        self.agent = Agent(
            resolved_model,
            deps_type=AgentRuntimeDeps,
            output_type=ExtractorOutput,
            instructions=(
                "Extract structured DS-160 claims from one user message. "
                "Never convert unknown into false. "
                "Use tools before asserting document-backed conclusions."
            ),
        )
        register_evidence_tools(self.agent)

    def run(self, *, deps: AgentRuntimeDeps, message_text: str, profile_payload: dict) -> ExtractorOutput:
        prompt = {"message_text": message_text, "profile": profile_payload}
        return self.agent.run_sync(str(prompt), deps=deps).output
```

```python
# app/services/consistency_service.py
import re

from app.agents.schemas import ConsistencyFinding
from app.domain.contracts import ApplicantProfile


class ConsistencyService:
    def evaluate(self, profile: ApplicantProfile) -> list[ConsistencyFinding]:
        findings: list[ConsistencyFinding] = []
        last_user_message = profile.ds160_view.get("last_user_message", "").lower()

        if any(re.search(pattern, last_user_message) is not None for pattern in (
            r"\\bi lied\\b",
            r"\\bi (?:used|submitted|provided|uploaded|brought) fake\\b",
            r"\\bi (?:forged|forge|faked)\\b",
        )):
            findings.append(
                ConsistencyFinding(
                    finding_type="hard_conflict",
                    severity="high",
                    status="confirmed",
                    summary="applicant self-reported false or fraudulent record",
                    evidence_refs=["msg:last_user_turn"],
                )
            )

        if (
            profile.funding.get("primary_source") == "parents"
            and not profile.field_provenance["/funding/primary_source"].evidence_refs
        ):
            findings.append(
                ConsistencyFinding(
                    finding_type="gap",
                    severity="medium",
                    status="supported",
                    summary="funding source claimed but not yet documented",
                    evidence_refs=["msg:last_user_turn"],
                )
            )
        return findings
```

```python
# app/services/extractor_service.py
from app.agents.extractor_agent import ExtractorAgentRunner
from app.agents.model_factory import AgentModelFactory


class ExtractorService:
    def __init__(self, db=None) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()

    def apply_message(self, profile, message_text: str):
        profile.ds160_view["last_user_message"] = message_text
        model, _runtime = self.model_factory.build("extractor_agent", "interview_turn")
        if model is None or self.db is None:
            return self._fallback_apply_message(profile, message_text)

        deps = AgentRuntimeDeps(
            session_id=profile.profile_id.replace("profile-", ""),
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
        )
        output = ExtractorAgentRunner(model=model).run(
            deps=deps,
            message_text=message_text,
            profile_payload=profile.model_dump(mode="json"),
        )
        return self._apply_output(profile, output)
```

- [ ] **Step 4: 运行 extractor 单测与 live extractor 适配测试**

Run: `uv run pytest tests/unit/test_extractor_service.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 extractor runtime 替换**

```bash
git add app/agents/extractor_agent.py app/services/extractor_service.py app/services/consistency_service.py tests/unit/test_extractor_service.py tests/integration/live/test_live_extractor_service.py
git commit -m "feat: replace extractor runtime with pydanticai"
```

## Task 5: 用 QuestionAgent 替换 next-question 生成，并切换 MessageService 主路径

**Files:**
- Create: `app/agents/question_agent.py`
- Modify: `app/services/message_service.py`
- Modify: `tests/integration/test_messages_api.py`
- Modify: `tests/integration/live/test_live_messages_api.py`
- Modify: `tests/integration/live/test_live_openai_compat.py`

- [ ] **Step 1: 写失败测试，锁定“message flow 由 QuestionAgent 生成下一步动作”**

```python
# tests/integration/test_messages_api.py
def test_message_turn_uses_question_agent_output_for_continue_interview(
    client: TestClient,
    monkeypatch,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    monkeypatch.setattr(
        "app.services.message_service.QuestionAgentRunner.run",
        lambda self, **kwargs: InterviewNextAction(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    assert response.json()["assistant_message"] == "What is the purpose of your travel?"
```

- [ ] **Step 2: 运行测试，确认 QuestionAgent path 还没接入**

Run: `uv run pytest tests/integration/test_messages_api.py -q`  
Expected: FAIL with import or attribute errors for `QuestionAgentRunner`

- [ ] **Step 3: 实现 QuestionAgentRunner，并让 MessageService 统一编排 extractor/scoring/question/govenor**

```python
# app/agents/question_agent.py
from pydantic_ai import Agent

from app.agents.model_factory import AgentModelFactory
from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.agents.tools import register_evidence_tools


class QuestionAgentRunner:
    def __init__(self, model=None) -> None:
        resolved_model = model
        if resolved_model is None:
            resolved_model, _runtime = AgentModelFactory().build("question_agent", "interview_turn")

        self.agent = Agent(
            resolved_model,
            deps_type=AgentRuntimeDeps,
            output_type=InterviewNextAction,
            instructions=(
                "Generate the next assistant action for the DS-160 simulator. "
                "Do not output refusal decisions; Governor remains authoritative. "
                "If more evidence is needed, requested_documents must be non-empty."
            ),
        )
        register_evidence_tools(self.agent)

    def run(self, *, deps: AgentRuntimeDeps, profile_payload: dict, score_payload: dict, governor_decision: str):
        prompt = {
            "profile": profile_payload,
            "score": score_payload,
            "governor_decision": governor_decision,
        }
        return self.agent.run_sync(str(prompt), deps=deps).output
```

```python
# app/services/message_service.py
from app.agents.question_agent import QuestionAgentRunner
from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.services.evidence_service import EvidenceService
from app.services.retrieval_service import RetrievalService


class MessageService:
    def handle_user_turn(self, session_id: str, message_text: str) -> dict:
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)

        profile = self._load_profile(record.session_id, record.profile_json)
        profile.profile_version += 1
        profile.visa_intent["declared_family"] = record.declared_family
        profile = self.extractor.apply_message(profile, message_text)
        findings = self.consistency.evaluate(profile)
        score = self.scoring.propose(profile, findings, scoring_stage="interview_turn")
        early_term_candidate = self._build_early_term_candidate(record.declared_family, score)
        governor = self.governor.decide(profile, score, early_term_candidate)

        action = self._question_action(record.session_id, profile, score, governor["decision"])
        record.profile_json = profile.model_dump(mode="json")
        record.current_governor_decision = governor["decision"]
        self.session_repo.save(record)
        return {
            "assistant_message": action.assistant_message,
            "governor_decision": governor["decision"],
            "score_summary": {
                "category_fit": score.category_fit,
                "document_readiness": score.document_readiness,
                "narrative_consistency": score.narrative_consistency,
                "confidence": score.confidence,
            },
            "requested_documents": action.requested_documents,
        }
```

- [ ] **Step 4: 运行消息流与 OpenAI-compatible live 测试**

Run: `uv run pytest tests/integration/test_messages_api.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 question/message runtime 替换**

```bash
git add app/agents/question_agent.py app/services/message_service.py tests/integration/test_messages_api.py tests/integration/live/test_live_messages_api.py tests/integration/live/test_live_openai_compat.py
git commit -m "feat: replace question flow with pydanticai"
```

## Task 6: 清理 live 覆盖、替换旧 runtime smoke test，并做总回归

**Files:**
- Modify: `tests/integration/live/test_live_llm_client.py`
- Modify: `tests/integration/live/test_live_extractor_service.py`
- Modify: `tests/integration/live/test_live_scoring_service.py`
- Modify: `tests/integration/live/test_live_messages_api.py`

- [ ] **Step 1: 写失败测试，锁定 provider compatibility 与新 runtime 主路径**

```python
# tests/integration/live/test_live_llm_client.py
import os

import pytest

from app.agents.model_factory import AgentModelFactory


@pytest.mark.live_llm
def test_live_model_factory_builds_openai_compatible_model() -> None:
    assert os.getenv("OPENAI_API_KEY")
    assert os.getenv("OPENAI_BASE_URL")

    model, runtime = AgentModelFactory().build("extractor_agent", "interview_turn")

    assert model is not None
    assert runtime["model"] == "gpt-5.4"
```

- [ ] **Step 2: 运行 live 测试文件，确认旧 `LLMClient` smoke test 已过时**

Run: `RUN_LIVE_LLM_TESTS=1 OPENAI_BASE_URL=... OPENAI_API_KEY=... uv run pytest tests/integration/live/test_live_llm_client.py -q -m live_llm`  
Expected: FAIL or require rewrite because old test is tied to `LLMClient.generate_json`

- [ ] **Step 3: 改写 live tests，让它们覆盖新 runtime，而不是旧抽象**

```python
# tests/integration/live/test_live_extractor_service.py
@pytest.mark.live_llm
def test_live_extractor_maps_parent_funding_with_agent_runtime(live_db_session_factory) -> None:
    profile = ApplicantProfile.minimal("profile-live-extractor-1")
    with live_db_session_factory() as db:
        updated = ExtractorService(db=db).apply_message(
            profile,
            "My mother and father will cover all my tuition and living expenses.",
        )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"
```

```python
# tests/integration/live/test_live_scoring_service.py
@pytest.mark.live_llm
def test_live_scoring_returns_structured_score_proposal_mapping(live_db_session_factory) -> None:
    profile = ApplicantProfile.minimal("profile-live-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"
    with live_db_session_factory() as db:
        score = ScoringService(db=db).propose(
            profile,
            findings=[
                ConsistencyFinding(
                    finding_type="gap",
                    severity="medium",
                    status="supported",
                    summary="funding source claimed but not yet documented",
                    evidence_refs=["msg:last_user_turn"],
                )
            ],
            scoring_stage="interview_turn",
        )

    assert score.document_readiness <= 40
```

```python
# tests/integration/live/test_live_messages_api.py
@pytest.mark.live_llm
def test_live_messages_api_waits_for_worker_before_continue(
    live_api_client,
    live_db_session_factory,
) -> None:
    session_resp = live_api_client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
    )
    live_api_client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )

    pre_worker = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )
    assert pre_worker.status_code == 200
    assert pre_worker.json()["governor_decision"] == "need_more_evidence"

    with live_db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    post_worker = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )
    assert post_worker.status_code == 200
    assert post_worker.json()["governor_decision"] == "continue_interview"
```

- [ ] **Step 4: 跑 Phase 2 总回归**

Run: `uv run pytest tests/unit/test_model_factory.py tests/unit/test_retrieval_service.py tests/unit/test_evidence_service.py tests/unit/test_agent_tools.py tests/unit/test_scoring_service.py tests/unit/test_extractor_service.py tests/integration/test_tool_based_scoring.py tests/integration/test_messages_api.py -q`  
Expected: PASS

Run: `uv run pytest -q -m "not live_llm"`  
Expected: PASS

Run: `RUN_LIVE_LLM_TESTS=1 OPENAI_BASE_URL=... OPENAI_API_KEY=... uv run pytest tests/integration/live -q -m live_llm`  
Expected: PASS

- [ ] **Step 5: 提交 Phase 2 runtime cutover**

```bash
git add tests/integration/live/test_live_llm_client.py tests/integration/live/test_live_extractor_service.py tests/integration/live/test_live_scoring_service.py tests/integration/live/test_live_messages_api.py
git commit -m "test: cover pydanticai runtime flows"
```

## Out of Scope for This Plan

以下内容明确不在本计划内：

- `Chainlit`
- `pydantic-graph` / `Temporal`
- `gate_review` 状态表
- `runtime_trace / score_history / governor_history` 持久化
- 删除旧 `LLMClient` 文件
- report trace 扩展

## Verification Commands

```bash
uv run pytest tests/unit/test_model_factory.py tests/unit/test_retrieval_service.py tests/unit/test_evidence_service.py tests/unit/test_agent_tools.py tests/unit/test_scoring_service.py tests/unit/test_extractor_service.py tests/integration/test_tool_based_scoring.py tests/integration/test_messages_api.py -q
uv run pytest -q -m "not live_llm"
RUN_LIVE_LLM_TESTS=1 OPENAI_BASE_URL=... OPENAI_API_KEY=... uv run pytest tests/integration/live -q -m live_llm
```

## Self-Review

### Spec coverage

- `PydanticAI` runtime foundation：Task 1 覆盖
- retrieval / evidence tools：Task 2 覆盖
- tool-based scoring：Task 3 覆盖
- tool-based extractor：Task 4 覆盖
- question flow / message orchestration：Task 5 覆盖
- live / non-live regressions：Task 6 覆盖
- `Chainlit` / durable execution / trace persistence 明确保留到后续阶段，不在本计划混入

### Placeholder scan

- 无 `TODO/TBD`
- 每个任务都给出具体文件、测试、命令与代码片段
- 没有使用“类似 Task N”这类跳转式描述

### Type consistency

- Phase 2 统一以 `app/agents/schemas.py` 作为 typed output 真源
- `ConsistencyService` 输出 `ConsistencyFinding`
- `ScoringAgentRunner` 输出 `ScoreProposal`
- `QuestionAgentRunner` 输出 `InterviewNextAction`
- `GovernorService` 仍消费现有 `ScoreState`
