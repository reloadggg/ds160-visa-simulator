from types import SimpleNamespace
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.agents.schemas import InterviewNextAction
from app.domain.contracts import ApplicantProfile, ScoreState
from app.services.interview_runtime_service import InterviewRuntimeService
from app.services.runtime_errors import ProviderAPIError


def test_question_action_raises_provider_api_error_on_auth_failure(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("test")
    score = ScoreState.minimal(1, "interview_turn")
    
    class FakeModelHTTPError(ModelHTTPError):
        def __init__(self):
            # ModelHTTPError(status_code, model_name, body)
            # Actually pydantic-ai ModelHTTPError signature might vary.
            # Let's just set the attributes.
            super().__init__("Auth failed")
            self.status_code = 401
            self.model_name = "test-model"
            self.body = {"code": "invalid_api_key"}
        
    def fake_run(*args, **kwargs):
        raise FakeModelHTTPError()
        
    monkeypatch.setattr("app.services.interview_runtime_service.QuestionAgentRunner.run", fake_run)
    monkeypatch.setattr("app.services.interview_runtime_service.QuestionAgentRunner.__init__", lambda self, model, instructions: None)
    
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
            super().__init__("Rate limit exceeded")
            self.status_code = 429
            self.model_name = "test-model"
            self.body = {"code": "insufficient_quota"}
        
    def fake_run(*args, **kwargs):
        raise FakeModelHTTPError()
        
    monkeypatch.setattr("app.services.interview_runtime_service.QuestionAgentRunner.run", fake_run)
    monkeypatch.setattr("app.services.interview_runtime_service.QuestionAgentRunner.__init__", lambda self, model, instructions: None)
    
    monkeypatch.setattr(service.model_factory, "build", lambda *a, **k: ("fake-model", {"provider": "openai", "model": "gpt-4o"}))
    monkeypatch.setattr(service.capability_orchestrator, "orchestrate", lambda *a, **k: SimpleNamespace(capability_plan=[], trace_entries=[], tool_outputs={}))
    monkeypatch.setattr(service, "_build_dynamic_turn_context", lambda *a, **k: {})
    monkeypatch.setattr(service, "_raise_if_question_model_unavailable", lambda *a, **k: None)
    
    with pytest.raises(ProviderAPIError) as exc_info:
        service._question_action("sess-1", profile, score, "continue_interview")
        
    assert exc_info.value.status_code == 429
    assert "额度已耗尽" in exc_info.value.detail


def test_question_action_still_falls_back_on_generic_error(monkeypatch) -> None:
    service = InterviewRuntimeService(db=object())
    profile = ApplicantProfile.minimal("test")
    score = ScoreState.minimal(1, "interview_turn")
    
    def fake_run(*args, **kwargs):
        raise ValueError("Something went wrong internally")
        
    monkeypatch.setattr("app.services.interview_runtime_service.QuestionAgentRunner.run", fake_run)
    monkeypatch.setattr("app.services.interview_runtime_service.QuestionAgentRunner.__init__", lambda self, model, instructions: None)
    
    monkeypatch.setattr(service.model_factory, "build", lambda *a, **k: ("fake-model", {"provider": "openai", "model": "gpt-4o", "fallback_messages": {}}))
    monkeypatch.setattr(service.capability_orchestrator, "orchestrate", lambda *a, **k: SimpleNamespace(capability_plan=[], trace_entries=[], tool_outputs={}))
    monkeypatch.setattr(service, "_build_dynamic_turn_context", lambda *a, **k: {})
    monkeypatch.setattr(service, "_raise_if_question_model_unavailable", lambda *a, **k: None)
    
    # It should not raise ProviderAPIError, but return a fallback action
    action, trace = service._question_action("sess-1", profile, score, "continue_interview")
    
    assert action.decision == "continue_interview"
    assert "What is the purpose of your travel?" in action.assistant_message
    assert trace.fallback_used is True
