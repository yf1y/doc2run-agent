"""Provider-neutral model settings and synchronous LiteLLM adapters."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class TextModel(Protocol):
    """The only model capability required by the agent graphs."""

    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


@dataclass(frozen=True)
class ModelSettings:
    """Provider-neutral configuration for one LiteLLM-backed model."""

    model: str
    api_base: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 3
    max_tokens: int = 4_000
    temperature: float = 0.0
    trust_env: bool = False
    context_tokens: int = 16_000

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if self.context_tokens < 1_000:
            raise ValueError("context_tokens must be at least 1000")
        if self.max_tokens >= self.context_tokens:
            raise ValueError("max_tokens must be smaller than context_tokens")

    @classmethod
    def from_env(cls) -> "ModelSettings":
        return _global_settings(require_model=True)


@dataclass(frozen=True)
class AgentModelSettings:
    """Independent model settings for the model-backed Chat, Code, and Fix stages."""

    chat: ModelSettings
    code: ModelSettings
    fix: ModelSettings

    @classmethod
    def from_env(cls) -> "AgentModelSettings":
        shared = _global_settings(require_model=False)
        return cls(
            chat=_role_settings("CHAT", shared),
            code=_role_settings("CODE", shared),
            fix=_role_settings("FIX", shared),
        )


@dataclass
class AgentModels:
    """The explicit models consumed by Chat, Code, and Fix."""

    chat: TextModel
    code: TextModel
    fix: TextModel
    _runtime: _LiteLLMRuntime | None = field(default=None, repr=False)

    @classmethod
    def shared(cls, model: TextModel) -> "AgentModels":
        return cls(chat=model, code=model, fix=model)

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            return
        closed: set[int] = set()
        for model in (self.chat, self.code, self.fix):
            close = getattr(model, "close", None)
            if callable(close) and id(model) not in closed:
                close()
                closed.add(id(model))

    def __enter__(self) -> "AgentModels":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _LiteLLMRuntime:
    """One shared HTTP transport for a set of LiteLLM model adapters."""

    def __init__(self, *, trust_env: bool, timeout_seconds: float) -> None:
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        import httpx
        import litellm

        self._litellm = litellm
        self._http_client = httpx.Client(timeout=timeout_seconds, trust_env=trust_env)
        litellm.client_session = self._http_client
        self.completion = litellm.completion

    def close(self) -> None:
        if self._litellm.client_session is self._http_client:
            self._litellm.client_session = None
        self._http_client.close()


class LiteLLMModel:
    """Synchronous LiteLLM adapter shared by the CLI and Python API."""

    def __init__(
        self,
        settings: ModelSettings,
        *,
        completion_fn: Callable[..., Any] | None = None,
        runtime: _LiteLLMRuntime | None = None,
    ) -> None:
        if completion_fn is not None and runtime is not None:
            raise ValueError("Provide completion_fn or runtime, not both")
        self.settings = settings
        self._owned_runtime: _LiteLLMRuntime | None = None
        if completion_fn is None:
            if runtime is None:
                runtime = _LiteLLMRuntime(
                    trust_env=settings.trust_env,
                    timeout_seconds=settings.timeout_seconds,
                )
                self._owned_runtime = runtime
            completion_fn = runtime.completion
        self._completion = completion_fn

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        arguments: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.temperature,
            "timeout": self.settings.timeout_seconds,
            "num_retries": self.settings.max_retries,
            "max_tokens": self.settings.max_tokens,
        }
        if self.settings.api_base:
            arguments["api_base"] = self.settings.api_base
        if self.settings.api_key:
            arguments["api_key"] = self.settings.api_key

        response = self._completion(**arguments)
        content = _response_content(response)
        if not content.strip():
            raise ValueError("LiteLLM returned an empty text response")
        return content

    def close(self) -> None:
        if self._owned_runtime is not None:
            self._owned_runtime.close()
            self._owned_runtime = None

    def __enter__(self) -> "LiteLLMModel":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def create_model(settings: ModelSettings | None = None) -> LiteLLMModel:
    """Create one model adapter for direct programmatic use."""

    return LiteLLMModel(settings or ModelSettings.from_env())


def create_agent_models(settings: AgentModelSettings | None = None) -> AgentModels:
    """Create the independently configured model set used by the workflow."""

    selected = settings or AgentModelSettings.from_env()
    all_settings = (selected.chat, selected.code, selected.fix)
    trust_values = {item.trust_env for item in all_settings}
    if len(trust_values) != 1:
        raise ValueError("All agent models must use the same trust_env setting")
    runtime = _LiteLLMRuntime(
        trust_env=selected.chat.trust_env,
        timeout_seconds=max(item.timeout_seconds for item in all_settings),
    )
    return AgentModels(
        chat=LiteLLMModel(selected.chat, runtime=runtime),
        code=LiteLLMModel(selected.code, runtime=runtime),
        fix=LiteLLMModel(selected.fix, runtime=runtime),
        _runtime=runtime,
    )


def as_agent_models(value: TextModel | AgentModels) -> AgentModels:
    return value if isinstance(value, AgentModels) else AgentModels.shared(value)


def _role_settings(role: str, shared: ModelSettings) -> ModelSettings:
    prefix = f"DOC2RUN_AGENT_{role}"
    model = _env_text(f"{prefix}_MODEL", fallback=shared.model)
    if not model:
        raise ValueError(f"{prefix}_MODEL cannot be empty")
    return ModelSettings(
        model=model,
        api_base=_env_optional_text(f"{prefix}_API_BASE", fallback=shared.api_base),
        api_key=_env_optional_text(f"{prefix}_API_KEY", fallback=shared.api_key),
        timeout_seconds=_positive_float_env(
            f"{prefix}_TIMEOUT",
            default=shared.timeout_seconds,
        ),
        max_retries=_nonnegative_int_env(
            f"{prefix}_MAX_RETRIES",
            default=shared.max_retries,
        ),
        max_tokens=_positive_int_env(
            f"{prefix}_MAX_TOKENS", default=shared.max_tokens, minimum=1
        ),
        temperature=shared.temperature,
        trust_env=shared.trust_env,
        context_tokens=_positive_int_env(
            f"{prefix}_CONTEXT_TOKENS", default=shared.context_tokens
        ),
    )


def _global_settings(*, require_model: bool) -> ModelSettings:
    model = os.getenv("DOC2RUN_AGENT_MODEL", "").strip()
    if require_model and not model:
        raise ValueError(
            "DOC2RUN_AGENT_MODEL is required; use a LiteLLM model name such as "
            "'openai/gpt-5', 'anthropic/claude-sonnet-4-5', or 'ollama/qwen2.5-coder'"
        )
    return ModelSettings(
        model=model,
        api_base=os.getenv("DOC2RUN_AGENT_API_BASE") or None,
        api_key=os.getenv("DOC2RUN_AGENT_API_KEY") or None,
        timeout_seconds=_positive_float_env("DOC2RUN_AGENT_MODEL_TIMEOUT", default=120.0),
        max_retries=_nonnegative_int_env("DOC2RUN_AGENT_MODEL_MAX_RETRIES", default=3),
        max_tokens=_positive_int_env(
            "DOC2RUN_AGENT_MODEL_MAX_TOKENS", default=4_000, minimum=1
        ),
        trust_env=_env_flag("DOC2RUN_AGENT_TRUST_ENV", default=False),
        context_tokens=_positive_int_env("DOC2RUN_AGENT_CONTEXT_TOKENS", default=16_000),
    )


def _response_content(response: Any) -> str:
    try:
        if isinstance(response, dict):
            content = response["choices"][0]["message"]["content"]
        else:
            content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as error:
        raise ValueError("LiteLLM response does not contain choices[0].message.content") from error

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_content_block_text(block) for block in content)
    if content is None:
        return ""
    return str(content)


def _content_block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        return str(block.get("text", ""))
    return str(getattr(block, "text", ""))


def _env_text(name: str, *, fallback: str) -> str:
    raw = os.getenv(name)
    return fallback if raw is None else raw.strip()


def _env_optional_text(name: str, *, fallback: str | None) -> str | None:
    raw = os.getenv(name)
    return fallback if raw is None else raw.strip() or None


def _positive_float_env(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _positive_int_env(name: str, *, default: int, minimum: int = 1000) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")
