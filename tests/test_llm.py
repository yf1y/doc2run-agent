"""Tests for provider-neutral LiteLLM settings and retry behavior."""

from types import SimpleNamespace

import pytest

from doc2run_agent.llm import AgentModelSettings, LiteLLMModel, ModelSettings


def test_model_settings_load_provider_neutral_environment(monkeypatch):
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL", "anthropic/claude-test")
    monkeypatch.setenv("DOC2RUN_AGENT_API_BASE", "http://model-gateway.local")
    monkeypatch.setenv("DOC2RUN_AGENT_API_KEY", "gateway-key")
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL_TIMEOUT", "45")
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL_MAX_RETRIES", "4")
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL_MAX_TOKENS", "2500")
    monkeypatch.setenv("DOC2RUN_AGENT_TRUST_ENV", "true")

    settings = ModelSettings.from_env()

    assert settings == ModelSettings(
        model="anthropic/claude-test",
        api_base="http://model-gateway.local",
        api_key="gateway-key",
        timeout_seconds=45,
        max_retries=4,
        max_tokens=2500,
        trust_env=True,
    )


def test_model_settings_require_litellm_model_name(monkeypatch):
    monkeypatch.delenv("DOC2RUN_AGENT_MODEL", raising=False)

    with pytest.raises(ValueError, match="DOC2RUN_AGENT_MODEL"):
        ModelSettings.from_env()


def test_agent_model_settings_allow_independent_model_url_key_and_limits(monkeypatch):
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL", "openai/default")
    monkeypatch.setenv("DOC2RUN_AGENT_API_BASE", "http://default.local/v1")
    monkeypatch.setenv("DOC2RUN_AGENT_API_KEY", "default-key")
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL_TIMEOUT", "100")
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL_MAX_RETRIES", "2")
    monkeypatch.setenv("DOC2RUN_AGENT_CHAT_MODEL", "anthropic/chat")
    monkeypatch.setenv("DOC2RUN_AGENT_CHAT_API_BASE", "http://chat.local")
    monkeypatch.setenv("DOC2RUN_AGENT_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("DOC2RUN_AGENT_CODE_MODEL", "openai/code")
    monkeypatch.setenv("DOC2RUN_AGENT_CODE_API_BASE", "http://code.local/v1")
    monkeypatch.setenv("DOC2RUN_AGENT_CODE_API_KEY", "code-key")
    monkeypatch.setenv("DOC2RUN_AGENT_CODE_TIMEOUT", "200")
    monkeypatch.setenv("DOC2RUN_AGENT_FIX_MODEL", "ollama/fix")
    monkeypatch.setenv("DOC2RUN_AGENT_FIX_API_BASE", "http://fix.local")
    monkeypatch.setenv("DOC2RUN_AGENT_FIX_API_KEY", "")
    monkeypatch.setenv("DOC2RUN_AGENT_FIX_MAX_RETRIES", "0")

    settings = AgentModelSettings.from_env()

    assert settings.chat == ModelSettings(
        model="anthropic/chat",
        api_base="http://chat.local",
        api_key="chat-key",
        timeout_seconds=100,
        max_retries=2,
    )
    assert settings.code == ModelSettings(
        model="openai/code",
        api_base="http://code.local/v1",
        api_key="code-key",
        timeout_seconds=200,
        max_retries=2,
    )
    assert settings.fix == ModelSettings(
        model="ollama/fix",
        api_base="http://fix.local",
        api_key=None,
        timeout_seconds=100,
        max_retries=0,
    )


def test_agent_models_do_not_require_global_model_when_all_roles_are_set(monkeypatch):
    monkeypatch.delenv("DOC2RUN_AGENT_MODEL", raising=False)
    monkeypatch.setenv("DOC2RUN_AGENT_CHAT_MODEL", "openai/chat")
    monkeypatch.setenv("DOC2RUN_AGENT_CODE_MODEL", "openai/code")
    monkeypatch.setenv("DOC2RUN_AGENT_FIX_MODEL", "openai/fix")

    settings = AgentModelSettings.from_env()

    assert settings.chat.model == "openai/chat"
    assert settings.code.model == "openai/code"
    assert settings.fix.model == "openai/fix"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DOC2RUN_AGENT_MODEL_TIMEOUT", "0", "must be positive"),
        ("DOC2RUN_AGENT_MODEL_TIMEOUT", "slow", "must be a number"),
        ("DOC2RUN_AGENT_MODEL_MAX_RETRIES", "-1", "cannot be negative"),
        ("DOC2RUN_AGENT_MODEL_MAX_RETRIES", "many", "must be an integer"),
        ("DOC2RUN_AGENT_MODEL_MAX_TOKENS", "0", "must be at least 1"),
        ("DOC2RUN_AGENT_MODEL_MAX_TOKENS", "many", "must be an integer"),
        ("DOC2RUN_AGENT_TRUST_ENV", "sometimes", "must be true or false"),
    ],
)
def test_model_settings_validate_numeric_values(monkeypatch, name, value, message):
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL", "openai/test-model")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        ModelSettings.from_env()


def test_litellm_model_passes_unified_completion_arguments():
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="generated text"))]
        )

    model = LiteLLMModel(
        ModelSettings(
            model="ollama/qwen2.5-coder",
            api_base="http://localhost:11434",
            timeout_seconds=30,
            max_retries=1,
        ),
        completion_fn=completion,
    )

    assert model.complete("system", "user") == "generated text"
    assert calls == [
        {
            "model": "ollama/qwen2.5-coder",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "temperature": 0.0,
            "timeout": 30,
            "num_retries": 0,
            "max_retries": 0,
            "max_tokens": 4000,
            "api_base": "http://localhost:11434",
        }
    ]


def test_litellm_model_accepts_dict_response_and_text_blocks():
    model = LiteLLMModel(
        ModelSettings(model="openai/test-model"),
        completion_fn=lambda **_: {
            "choices": [{"message": {"content": [{"text": "first"}, {"text": " second"}]}}]
        },
    )

    assert model.complete("system", "user") == "first second"


def test_litellm_model_rejects_missing_text_response():
    model = LiteLLMModel(
        ModelSettings(model="openai/test-model"),
        completion_fn=lambda **_: {"choices": []},
    )

    with pytest.raises(ValueError, match=r"choices\[0\]"):
        model.complete("system", "user")


class HTTPFailure(Exception):
    def __init__(self, status):
        self.status_code = status


@pytest.mark.parametrize("status,expected", [(400, 1), (401, 1), (403, 1), (429, 3), (503, 3)])
def test_model_retries_only_transient_failures(status, expected, monkeypatch):
    monkeypatch.setattr("doc2run_agent.llm.time.sleep", lambda _: None)
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        raise HTTPFailure(status)
    model = LiteLLMModel(ModelSettings(model="fake", max_retries=2), completion_fn=completion)
    with pytest.raises(HTTPFailure):
        model.complete("system", "user")
    assert len(calls) == expected
    assert all(call["num_retries"] == call["max_retries"] == 0 for call in calls)


def test_model_transient_failure_then_success_records_usage(monkeypatch):
    from doc2run_agent.runtime.control import run_scope
    monkeypatch.setattr("doc2run_agent.llm.time.sleep", lambda _: None)
    responses = iter([HTTPFailure(503), {"choices": [{"message": {"content": "ok"}}],
                                       "usage": {"total_tokens": 7}}])
    def completion(**kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result
    model = LiteLLMModel(ModelSettings(model="fake", max_retries=2), completion_fn=completion)
    events = []
    with run_scope(10, events.append, lambda _: None):
        assert model.complete("s", "u") == "ok"
    assert len([event for event in events if event["status"] == "retrying"]) == 1
    assert events[-1]["usage"] == {"total_tokens": 7}


def test_model_deadline_prevents_retry_after_timeout():
    import threading
    from doc2run_agent.runtime.control import TaskTimeoutError, run_scope
    release = threading.Event()
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        release.wait(2)
        return {"choices": [{"message": {"content": "late"}}]}
    model = LiteLLMModel(ModelSettings(model="fake", max_retries=3), completion_fn=completion)
    try:
        with run_scope(0.1, lambda _: None, lambda _: None):
            with pytest.raises(TaskTimeoutError):
                model.complete("s", "u")
        assert len(calls) == 1
    finally:
        release.set()


@pytest.mark.parametrize("status,expected", [(401, 1), (503, 2)])
def test_real_http_adapter_does_not_stack_sdk_retries(model_http_server, status, expected):
    from doc2run_agent.llm import create_model
    base, replies, requests = model_http_server
    replies.extend([(status, "temporary failure"), (200, "中文回复正常")])
    with create_model(ModelSettings(model="openai/local-test", api_base=base,
                                   api_key="test-placeholder", max_retries=1, timeout_seconds=30)) as model:
        if status == 401:
            with pytest.raises(Exception) as error:
                model.complete("system", "user")
            assert error.value.status_code == 401
        else:
            assert model.complete("system", "user") == "中文回复正常"
    assert len(requests) == expected
