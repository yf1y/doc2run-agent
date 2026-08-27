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
            "num_retries": 1,
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
