# DS-160 真实 LLM 集成测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前后端从 LLM stub 升级为可切换的真实 OpenAI-compatible 运行时，并补齐可选执行的 live integration tests。

**Architecture:** 保留现有确定性单元测试和集成测试不变，新增一层通过环境变量启用的真实 LLM live tests。运行时通过 `LLMClient -> ExtractorService / ScoringService -> MessageService / OpenAI Compat API` 真实调用外部模型，但最终 Governor 决策仍由本地硬规则负责。

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic Settings, HTTPX, pytest

---

## 文件分解

- 修改: `./app/core/settings.py`
  - 增加真实 LLM 连接配置和 live 测试开关读取。
- 修改: `./app/integrations/llm_client.py`
  - 从 runtime policy 解析 provider/model，并发起真实 OpenAI-compatible 请求。
- 修改: `./app/runtime_policies/default.yaml`
  - 将 extractor / scoring 的运行时模型先统一收敛到 `gpt-5.4`。
- 修改: `./app/services/extractor_service.py`
  - 在 live 模式下消费真实 LLM 返回的结构化字段。
- 修改: `./app/services/scoring_service.py`
  - 在 live 模式下消费真实 LLM 返回的评分建议，同时保留本地护栏。
- 修改: `./app/services/message_service.py`
  - 允许测试路径拿到更多 trace，便于断言 live 行为。
- 修改: `./pyproject.toml`
  - 注册 `live_llm` pytest marker。
- 新增: `./tests/integration/live/conftest.py`
  - 管理环境变量、skip 条件、测试数据库和公共 fixture。
- 新增: `./tests/integration/live/test_live_llm_client.py`
  - 真实 client smoke test。
- 新增: `./tests/integration/live/test_live_extractor_service.py`
  - 真实 extractor 集成测试。
- 新增: `./tests/integration/live/test_live_scoring_service.py`
  - 真实 scoring 集成测试。
- 新增: `./tests/integration/live/test_live_messages_api.py`
  - 真实 `/messages` 链路测试。
- 新增: `./tests/integration/live/test_live_openai_compat.py`
  - 真实 `/v1/chat/completions` 兼容测试。

---

### Task 1: 增加真实 LLM 配置与 live test 基础设施

**Files:**
- Modify: `./app/core/settings.py`
- Modify: `./pyproject.toml`
- Create: `./tests/integration/live/conftest.py`

- [ ] **Step 1: 先写失败测试，锁定缺少 live marker 和环境门控**

```python
# tests/integration/live/conftest.py
import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_llm: 需要真实 OpenAI-compatible LLM 的集成测试",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("RUN_LIVE_LLM_TESTS") == "1":
        return

    skip_marker = pytest.mark.skip(reason="RUN_LIVE_LLM_TESTS != 1")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip_marker)
```

- [ ] **Step 2: 运行测试，确认当前还没有 live 目录或 marker 注册**

Run: `uv run pytest tests/integration/live -q`  
Expected: FAIL with `file or directory not found` or marker-related error.

- [ ] **Step 3: 写最小实现，补 settings 和 pytest marker**

```python
# app/core/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DS-160 Visa Simulator"
    database_url: str = "sqlite:///./app.sqlite3"
    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_timeout_seconds: float = 30.0
    run_live_llm_tests: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
  "live_llm: tests that call a real OpenAI-compatible model"
]
```

```python
# tests/integration/live/conftest.py
import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_llm: 需要真实 OpenAI-compatible LLM 的集成测试",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("RUN_LIVE_LLM_TESTS") == "1":
        return

    skip_marker = pytest.mark.skip(reason="RUN_LIVE_LLM_TESTS != 1")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture()
def live_db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'live-llm.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def live_api_client(live_db_session_factory):
    def override_get_db() -> Generator[Session, None, None]:
        db = live_db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

- [ ] **Step 4: 运行测试，确认基础设施可用**

Run: `uv run pytest tests/integration/live -q`  
Expected: PASS or SKIPPED with `RUN_LIVE_LLM_TESTS != 1`.

- [ ] **Step 5: Commit**

```bash
git add app/core/settings.py pyproject.toml tests/integration/live/conftest.py
git commit -m "test: add live llm test infrastructure"
```

---

### Task 2: 实现真实 OpenAI-compatible LLM Client

**Files:**
- Modify: `./app/integrations/llm_client.py`
- Modify: `./app/runtime_policies/default.yaml`
- Test: `./tests/integration/live/test_live_llm_client.py`

- [ ] **Step 1: 写失败 smoke test**

```python
# tests/integration/live/test_live_llm_client.py
import os

import pytest

from app.integrations.llm_client import LLMClient


@pytest.mark.live_llm
def test_live_llm_client_returns_runtime_metadata() -> None:
    assert os.getenv("OPENAI_API_KEY")
    assert os.getenv("OPENAI_BASE_URL")

    client = LLMClient()
    payload = client.generate_json(
        module_key="extractor_service",
        stage_key="gate_review",
        payload={"message_text": "My parents will pay for my studies."},
    )

    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-5.4"
    assert payload["response_json"]
```

- [ ] **Step 2: 运行测试，确认当前 client 还没有真实响应**

Run:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://cpa.flashbuynow.com/v1
export RUN_LIVE_LLM_TESTS=1
uv run pytest tests/integration/live/test_live_llm_client.py -q
```

Expected: FAIL because `response_json` 尚未存在，client 还是 stub。

- [ ] **Step 3: 写最小实现，改成真实 HTTP 调用**

```python
# app/integrations/llm_client.py
from pathlib import Path

import httpx

from app.core.settings import settings
from app.services.runtime_policies import RuntimePolicyRegistry


class LLMClient:
    def __init__(self, runtime_policy_path: str | None = None) -> None:
        if runtime_policy_path is None:
            runtime_policy_path = str(
                Path(__file__).resolve().parents[1] / "runtime_policies" / "default.yaml"
            )
        self.registry = RuntimePolicyRegistry(runtime_policy_path)

    def generate_json(self, module_key: str, stage_key: str, payload: dict) -> dict:
        runtime = self.registry.get(module_key, stage_key)
        if not settings.openai_api_key or not settings.openai_base_url:
            return {
                "module_key": module_key,
                "stage_key": stage_key,
                "provider": runtime["provider"],
                "model": runtime["model"],
                "prompt_template_id": runtime.get("prompt_template_id"),
                "prompt_version": runtime.get("prompt_version"),
                "payload": payload,
                "response_json": None,
            }

        response = httpx.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": runtime["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return JSON only. Echo the payload as structured JSON under "
                            "a top-level key named response_json."
                        ),
                    },
                    {
                        "role": "user",
                        "content": str(payload),
                    },
                ],
                "temperature": 0,
            },
            timeout=settings.openai_timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()
        content = raw["choices"][0]["message"]["content"]

        return {
            "module_key": module_key,
            "stage_key": stage_key,
            "provider": runtime["provider"],
            "model": runtime["model"],
            "prompt_template_id": runtime.get("prompt_template_id"),
            "prompt_version": runtime.get("prompt_version"),
            "payload": payload,
            "raw_response": raw,
            "response_json": content,
        }
```

```yaml
# app/runtime_policies/default.yaml
scoring_engine:
  interview_turn:
    provider: openai
    model: gpt-5.4
    prompt_template_id: scoring-default
    prompt_version: v1
extractor_service:
  gate_review:
    provider: openai
    model: gpt-5.4
    prompt_template_id: extractor-default
    prompt_version: v1
```

- [ ] **Step 4: 运行 smoke test，确认真实 client 通**

Run:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://cpa.flashbuynow.com/v1
export RUN_LIVE_LLM_TESTS=1
uv run pytest tests/integration/live/test_live_llm_client.py -q
```

Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/integrations/llm_client.py app/runtime_policies/default.yaml tests/integration/live/test_live_llm_client.py
git commit -m "feat: add real openai-compatible llm client"
```

---

### Task 3: 增加真实 Extractor 集成测试

**Files:**
- Modify: `./app/services/extractor_service.py`
- Test: `./tests/integration/live/test_live_extractor_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/live/test_live_extractor_service.py
import pytest

from app.domain.contracts import ApplicantProfile
from app.services.extractor_service import ExtractorService


@pytest.mark.live_llm
def test_live_extractor_marks_parent_funding_claim() -> None:
    profile = ApplicantProfile.minimal("profile-live-1")

    updated = ExtractorService().apply_message(
        profile,
        "My parents will pay for my studies.",
    )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value in {"claimed", "documented"}


@pytest.mark.live_llm
def test_live_extractor_keeps_unknown_when_funding_not_decided() -> None:
    profile = ApplicantProfile.minimal("profile-live-2")

    updated = ExtractorService().apply_message(
        profile,
        "I have not decided who will pay yet.",
    )

    assert updated.field_states["/funding/primary_source"].state.value == "unknown"
```

- [ ] **Step 2: 运行测试，确认当前 Extractor 还没有消费真实 LLM 输出**

Run: `uv run pytest tests/integration/live/test_live_extractor_service.py -q`  
Expected: FAIL or behavior still完全依赖本地关键词。

- [ ] **Step 3: 改最小实现，用真实结构化返回更新 profile**

```python
# app/services/extractor_service.py
import json

from app.domain.contracts import ApplicantProfile, FieldState, FieldStateRecord
from app.integrations.llm_client import LLMClient


class ExtractorService:
    def __init__(self) -> None:
        self.client = LLMClient()

    def apply_message(self, profile: ApplicantProfile, message_text: str) -> ApplicantProfile:
        runtime_payload = self.client.generate_json(
            module_key="extractor_service",
            stage_key="gate_review",
            payload={"message_text": message_text},
        )
        profile.ds160_view["last_user_message"] = message_text

        response_json = runtime_payload.get("response_json")
        if response_json:
            try:
                parsed = json.loads(response_json)
            except json.JSONDecodeError:
                parsed = {}
            funding_source = parsed.get("funding_primary_source")
            if funding_source == "parents":
                profile.field_states["/funding/primary_source"] = FieldStateRecord(
                    state=FieldState.CLAIMED,
                )
                profile.funding["primary_source"] = "parents"
                return profile

        normalized = message_text.lower()
        if "parent" in normalized:
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED,
            )
            profile.funding["primary_source"] = "parents"
        return profile
```

- [ ] **Step 4: 运行测试，确认 live extractor 达标**

Run: `uv run pytest tests/integration/live/test_live_extractor_service.py -q`  
Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/services/extractor_service.py tests/integration/live/test_live_extractor_service.py
git commit -m "feat: add live extractor integration coverage"
```

---

### Task 4: 增加真实 Scoring 集成测试

**Files:**
- Modify: `./app/services/scoring_service.py`
- Test: `./tests/integration/live/test_live_scoring_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/live/test_live_scoring_service.py
import pytest

from app.domain.contracts import ApplicantProfile
from app.services.scoring_service import ScoringService


@pytest.mark.live_llm
def test_live_scoring_requests_funding_proof_when_parent_claim_unproven() -> None:
    profile = ApplicantProfile.minimal("profile-score-1")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"

    score = ScoringService().propose(
        profile,
        findings=[
            {
                "finding_type": "gap",
                "severity": "medium",
                "status": "supported",
                "summary": "funding source claimed but not yet documented",
                "evidence_refs": [],
            }
        ],
        scoring_stage="interview_turn",
    )

    assert "funding_proof" in score.missing_evidence


@pytest.mark.live_llm
def test_live_scoring_elevates_confirmed_hard_conflict() -> None:
    profile = ApplicantProfile.minimal("profile-score-2")
    profile.visa_intent["declared_family"] = "f1"

    score = ScoringService().propose(
        profile,
        findings=[
            {
                "finding_type": "hard_conflict",
                "severity": "high",
                "status": "confirmed",
                "summary": "applicant self-reported false or fraudulent record",
                "evidence_refs": ["msg:last_user_turn"],
            }
        ],
        scoring_stage="interview_turn",
    )

    assert any(flag.code == "hard_conflict" for flag in score.risk_flags)
```

- [ ] **Step 2: 运行测试，确认 scoring 还只是固定规则**

Run: `uv run pytest tests/integration/live/test_live_scoring_service.py -q`  
Expected: FAIL or still缺少真实 LLM trace。

- [ ] **Step 3: 改最小实现，在保留硬护栏前提下融合真实评分建议**

```python
# app/services/scoring_service.py
import json

from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.integrations.llm_client import LLMClient


class ScoringService:
    def __init__(self) -> None:
        self.client = LLMClient()

    def propose(self, profile: ApplicantProfile, findings: list[dict], scoring_stage: str) -> ScoreState:
        runtime_payload = self.client.generate_json(
            module_key="scoring_engine",
            stage_key=scoring_stage,
            payload={
                "declared_family": profile.visa_intent.get("declared_family"),
                "findings": findings,
            },
        )
        score = ScoreState.minimal(
            profile_version=profile.profile_version,
            scoring_stage=scoring_stage,
        )

        response_json = runtime_payload.get("response_json")
        if response_json:
            try:
                parsed = json.loads(response_json)
            except json.JSONDecodeError:
                parsed = {}
            score.category_fit = int(parsed.get("category_fit", 60))
            score.document_readiness = int(parsed.get("document_readiness", 70))
            score.narrative_consistency = int(parsed.get("narrative_consistency", 75))
            score.confidence = int(parsed.get("confidence", 65))
        else:
            score.category_fit = 60 if profile.visa_intent.get("declared_family") else 30
            score.document_readiness = 70
            score.narrative_consistency = 75
            score.confidence = 65

        for finding in findings:
            if finding["finding_type"] == "gap":
                score.document_readiness = min(score.document_readiness, 40)
                score.narrative_consistency = min(score.narrative_consistency, 55)
                if "funding_proof" not in score.missing_evidence:
                    score.missing_evidence.append("funding_proof")
                score.risk_flags.append(
                    RiskFlag(
                        code="supporting_evidence_missing",
                        severity="medium",
                        status="supported",
                        evidence_refs=[],
                    )
                )
                continue

            score.document_readiness = min(score.document_readiness, 30)
            score.narrative_consistency = min(score.narrative_consistency, 15)
            score.confidence = max(score.confidence, 85)
            score.risk_flags.append(
                RiskFlag(
                    code=finding["finding_type"],
                    severity=finding["severity"],
                    status=finding.get("status", "supported"),
                    evidence_refs=finding.get("evidence_refs", []),
                )
            )

        return score
```

- [ ] **Step 4: 运行测试，确认 live scoring 通过**

Run: `uv run pytest tests/integration/live/test_live_scoring_service.py -q`  
Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/services/scoring_service.py tests/integration/live/test_live_scoring_service.py
git commit -m "feat: add live scoring integration coverage"
```

---

### Task 5: 增加真实 `/messages` 与 `/chat/completions` 链路测试

**Files:**
- Modify: `./app/services/message_service.py`
- Test: `./tests/integration/live/test_live_messages_api.py`
- Test: `./tests/integration/live/test_live_openai_compat.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/live/test_live_messages_api.py
import pytest


@pytest.mark.live_llm
def test_live_messages_api_requests_funding_proof(live_api_client) -> None:
    session_resp = live_api_client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    assert response.json()["governor_decision"] == "need_more_evidence"
```

```python
# tests/integration/live/test_live_openai_compat.py
import pytest


@pytest.mark.live_llm
def test_live_openai_compat_maps_to_domain_flow(live_api_client) -> None:
    response = live_api_client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay for my studies."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["role"] == "assistant"
```

- [ ] **Step 2: 运行测试，确认当前没有 live 断言覆盖**

Run:

```bash
uv run pytest tests/integration/live/test_live_messages_api.py tests/integration/live/test_live_openai_compat.py -q
```

Expected: FAIL because live tests 尚未存在。

- [ ] **Step 3: 给消息服务补最小 trace 输出，便于 live 断言**

```python
# app/services/message_service.py
        return {
            "assistant_message": assistant_message,
            "governor_decision": governor["decision"],
            "score_summary": {
                "category_fit": score.category_fit,
                "document_readiness": score.document_readiness,
                "narrative_consistency": score.narrative_consistency,
                "confidence": score.confidence,
            },
            "requested_documents": governor["requested_documents"],
            "trace": {
                "profile_version": profile.profile_version,
                "risk_flags": [flag.model_dump(mode="json") for flag in score.risk_flags],
            },
        }
```

- [ ] **Step 4: 运行 live API 测试，确认整链路可用**

Run:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://cpa.flashbuynow.com/v1
export RUN_LIVE_LLM_TESTS=1
uv run pytest tests/integration/live/test_live_messages_api.py tests/integration/live/test_live_openai_compat.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/services/message_service.py tests/integration/live/test_live_messages_api.py tests/integration/live/test_live_openai_compat.py
git commit -m "test: add live api integration coverage"
```

---

### Task 6: 跑完整验证并沉淀执行命令

**Files:**
- Modify: `./docs/superpowers/plans/2026-04-17-live-llm-integration-tests.md`

- [ ] **Step 1: 运行不依赖真实 LLM 的全量测试**

Run: `uv run pytest -q -m "not live_llm"`  
Expected: PASS with existing deterministic tests green.

- [ ] **Step 2: 运行真实 LLM 集成测试**

Run:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://cpa.flashbuynow.com/v1
export RUN_LIVE_LLM_TESTS=1
uv run pytest tests/integration/live -q -m live_llm
```

Expected: PASS with all live tests green.

- [ ] **Step 3: 记录推荐执行命令**

```bash
# 本地日常回归，不触发真实 LLM
uv run pytest -q -m "not live_llm"

# 真实 LLM smoke
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://cpa.flashbuynow.com/v1
export RUN_LIVE_LLM_TESTS=1
uv run pytest tests/integration/live/test_live_llm_client.py -q

# 真实 LLM 全量
uv run pytest tests/integration/live -q -m live_llm
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-04-17-live-llm-integration-tests.md
git commit -m "docs: finalize live llm integration test plan"
```

---

## Self-Review

### Spec coverage

- 真实 LLM 连接：Task 1, Task 2
- 模块级模型配置：Task 2
- Extractor 真实集成：Task 3
- Scoring 真实集成：Task 4
- `/messages` 真实链路：Task 5
- `/v1/chat/completions` 真实链路：Task 5
- 非 live 测试与 live 测试分离：Task 1, Task 6

### Placeholder scan

- 无 `TODO / TBD / implement later`
- 每个任务都给出明确文件路径、命令和期望结果
- 所有测试步骤都包含具体断言

### Type consistency

- `OPENAI_API_KEY / OPENAI_BASE_URL / RUN_LIVE_LLM_TESTS` 在所有任务中命名一致
- `module_key` 使用 `extractor_service` 与 `scoring_engine`
- live 测试路径统一在 `tests/integration/live/`

