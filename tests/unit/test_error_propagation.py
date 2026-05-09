from types import SimpleNamespace
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.agents.schemas import InterviewNextAction
from app.domain.contracts import ApplicantProfile, ScoreState
from app.services.interview_runtime_service import InterviewRuntimeService
from app.services.runtime_errors import ModelRuntimeError, ProviderAPIError


def test_question_action_raises_provider_api_error_on_auth_failure(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("test")
    score = ScoreState.minimal(1, "interview_turn")
    
    class FakeModelHTTPError(ModelHTTPError):
        def __init__(self):
            super().__init__("Auth failed", model_name="test-model", body={"code": "invalid_api_key"})
            self.status_code = 401
        
    def fake_run(*args, **kwargs):
        raise FakeModelHTTPError()
        
    monkeypatch.setattr("app.services.interview_runtime_service.AdjudicationAgentRunner.run", fake_run)
    monkeypatch.setattr("app.services.interview_runtime_service.AdjudicationAgentRunner.__init__", lambda self, model, instructions: None)
    
    monkeypatch.setattr(service.model_factory, "build", lambda *a, **k: ("fake-model", {"provider": "openai", "model": "gpt-4o"}))
    monkeypatch.setattr(service.capability_orchestrator, "orchestrate", lambda *a, **k: SimpleNamespace(capability_plan=[], trace_entries=[], tool_outputs={}))
    monkeypatch.setattr(service, "_build_dynamic_turn_context", lambda *a, **k: {})
    monkeypatch.setattr(service, "_raise_if_question_model_unavailable", lambda *a, **k: None)
    
    with pytest.raises(ProviderAPIError) as exc_info:
        service._question_action("sess-1", profile, score, "continue_interview")
        
    assert exc_info.value.status_code == 401
    assert "认证失败" in exc_info.value.detail


def test_question_action_raises_provider_api_error_on_rate_limit(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("test")
    score = ScoreState.minimal(1, "interview_turn")
    
    class FakeModelHTTPError(ModelHTTPError):
        def __init__(self):
            super().__init__("Rate limit exceeded", model_name="test-model", body={"code": "insufficient_quota"})
            self.status_code = 429
        
    def fake_run(*args, **kwargs):
        raise FakeModelHTTPError()
        
    monkeypatch.setattr("app.services.interview_runtime_service.AdjudicationAgentRunner.run", fake_run)
    monkeypatch.setattr("app.services.interview_runtime_service.AdjudicationAgentRunner.__init__", lambda self, model, instructions: None)
    
    monkeypatch.setattr(service.model_factory, "build", lambda *a, **k: ("fake-model", {"provider": "openai", "model": "gpt-4o"}))
    monkeypatch.setattr(service.capability_orchestrator, "orchestrate", lambda *a, **k: SimpleNamespace(capability_plan=[], trace_entries=[], tool_outputs={}))
    monkeypatch.setattr(service, "_build_dynamic_turn_context", lambda *a, **k: {})
    monkeypatch.setattr(service, "_raise_if_question_model_unavailable", lambda *a, **k: None)
    
    with pytest.raises(ProviderAPIError) as exc_info:
        service._question_action("sess-1", profile, score, "continue_interview")
        
    assert exc_info.value.status_code == 429
    assert "额度已耗尽" in exc_info.value.detail


def test_question_action_raises_runtime_error_on_generic_error(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("test")
    score = ScoreState.minimal(1, "interview_turn")
    
    def fake_run(*args, **kwargs):
        raise ValueError("Something went wrong internally")
        
    monkeypatch.setattr("app.services.interview_runtime_service.AdjudicationAgentRunner.run", fake_run)
    monkeypatch.setattr("app.services.interview_runtime_service.AdjudicationAgentRunner.__init__", lambda self, model, instructions: None)
    
    monkeypatch.setattr(service.model_factory, "build", lambda *a, **k: ("fake-model", {"provider": "openai", "model": "gpt-4o"}))
    monkeypatch.setattr(service.capability_orchestrator, "orchestrate", lambda *a, **k: SimpleNamespace(capability_plan=[], trace_entries=[], tool_outputs={}))
    monkeypatch.setattr(service, "_build_dynamic_turn_context", lambda *a, **k: {})
    monkeypatch.setattr(service, "_raise_if_question_model_unavailable", lambda *a, **k: None)

    with pytest.raises(ModelRuntimeError) as exc_info:
        service._question_action("sess-1", profile, score, "continue_interview")

    assert exc_info.value.status_code == 503
    assert "运行失败" in exc_info.value.detail
