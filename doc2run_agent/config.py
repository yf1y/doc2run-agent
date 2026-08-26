from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .llm import AgentModelSettings, ModelSettings


DEFAULT_CONFIG_NAME = "doc2run_agent.yaml"
ROLE_NAMES = ("requirements", "code", "fix")


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)
    context_tokens: int | None = Field(default=None, ge=1000)

    @model_validator(mode="after")
    def validate_secret_source(self) -> "ModelConfig":
        if self.api_key is not None and self.api_key_env is not None:
            raise ValueError("api_key and api_key_env cannot both be set")
        return self


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: ModelConfig = Field(default_factory=ModelConfig)
    requirements: ModelConfig = Field(default_factory=ModelConfig)
    code: ModelConfig = Field(default_factory=ModelConfig)
    fix: ModelConfig = Field(default_factory=ModelConfig)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: ModelsConfig = Field(default_factory=ModelsConfig)


def load_agent_model_settings(config_path: str | Path | None = None) -> AgentModelSettings:
    """Load `.env`, then resolve YAML over environment-variable defaults."""

    path = _resolve_config_path(config_path)
    dotenv_directory = path.parent if path is not None else Path.cwd()
    load_dotenv(dotenv_directory / ".env", override=False)
    if path is None:
        return AgentModelSettings.from_env()

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a YAML object")
    config = ProjectConfig.model_validate(raw)
    return AgentModelSettings(
        requirements=_resolve_role("requirements", config.models),
        code=_resolve_role("code", config.models),
        fix=_resolve_role("fix", config.models),
    )


def _resolve_config_path(config_path: str | Path | None) -> Path | None:
    if config_path is not None:
        path = Path(config_path)
        if not path.is_file():
            raise ValueError(f"Configuration file does not exist: {path}")
        return path
    default = Path.cwd() / DEFAULT_CONFIG_NAME
    return default if default.is_file() else None


def _resolve_role(role: str, models: ModelsConfig) -> ModelSettings:
    role_config = getattr(models, role)
    env_prefix = f"DOC2RUN_AGENT_{role.upper()}"
    model = _first_text(
        role_config.model,
        models.defaults.model,
        os.getenv(f"{env_prefix}_MODEL"),
        os.getenv("DOC2RUN_AGENT_MODEL"),
    )
    if not model:
        raise ValueError(
            f"models.{role}.model is required when no YAML default or environment fallback exists"
        )

    api_base = _first_optional_text(
        role_config.api_base,
        models.defaults.api_base,
        os.getenv(f"{env_prefix}_API_BASE"),
        os.getenv("DOC2RUN_AGENT_API_BASE"),
    )
    api_key = _resolve_api_key(role_config, models.defaults, env_prefix)
    timeout = _first_number(
        role_config.timeout,
        models.defaults.timeout,
        os.getenv(f"{env_prefix}_TIMEOUT"),
        os.getenv("DOC2RUN_AGENT_MODEL_TIMEOUT"),
        default=120.0,
        field_name=f"models.{role}.timeout",
    )
    max_retries = _first_integer(
        role_config.max_retries,
        models.defaults.max_retries,
        os.getenv(f"{env_prefix}_MAX_RETRIES"),
        os.getenv("DOC2RUN_AGENT_MODEL_MAX_RETRIES"),
        default=3,
        field_name=f"models.{role}.max_retries",
    )
    max_tokens = _first_integer(
        role_config.max_tokens,
        models.defaults.max_tokens,
        os.getenv(f"{env_prefix}_MAX_TOKENS"),
        os.getenv("DOC2RUN_AGENT_MODEL_MAX_TOKENS"),
        default=4_000,
        field_name=f"models.{role}.max_tokens",
    )
    if max_tokens < 1:
        raise ValueError(f"models.{role}.max_tokens must be positive")
    context_tokens = _first_integer(
        role_config.context_tokens,
        models.defaults.context_tokens,
        os.getenv(f"{env_prefix}_CONTEXT_TOKENS"),
        os.getenv("DOC2RUN_AGENT_CONTEXT_TOKENS"),
        default=16_000,
        field_name=f"models.{role}.context_tokens",
    )
    if context_tokens < 1000:
        raise ValueError(f"models.{role}.context_tokens must be at least 1000")
    if max_tokens >= context_tokens:
        raise ValueError(f"models.{role}.max_tokens must be smaller than context_tokens")
    return ModelSettings(
        model=model,
        api_base=api_base,
        api_key=api_key,
        timeout_seconds=timeout,
        max_retries=max_retries,
        max_tokens=max_tokens,
        trust_env=_environment_flag("DOC2RUN_AGENT_TRUST_ENV", default=False),
        context_tokens=context_tokens,
    )


def _resolve_api_key(role: ModelConfig, defaults: ModelConfig, env_prefix: str) -> str | None:
    source = role if role.api_key is not None or role.api_key_env is not None else defaults
    if source.api_key_env is not None:
        value = os.getenv(source.api_key_env, "").strip()
        if not value:
            raise ValueError(f"Environment variable '{source.api_key_env}' is required by api_key_env")
        return value
    if source.api_key is not None:
        return source.api_key.strip() or None
    return _first_optional_text(
        os.getenv(f"{env_prefix}_API_KEY"),
        os.getenv("DOC2RUN_AGENT_API_KEY"),
    )


def _first_text(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None


def _first_optional_text(*values: str | None) -> str | None:
    for value in values:
        if value is not None:
            return value.strip() or None
    return None


def _first_number(
    *values: Any,
    default: float,
    field_name: str,
) -> float:
    for value in values:
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} must be a number") from None
        if parsed <= 0:
            raise ValueError(f"{field_name} must be positive")
        return parsed
    return default


def _first_integer(
    *values: Any,
    default: int,
    field_name: str,
) -> int:
    for value in values:
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} must be an integer") from None
        if parsed < 0:
            raise ValueError(f"{field_name} cannot be negative")
        return parsed
    return default


def _environment_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")
