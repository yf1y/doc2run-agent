import os

import pytest
from pydantic import ValidationError

from doc2run_agent.config import load_agent_model_settings


def test_yaml_and_sibling_dotenv_override_environment_fallbacks(tmp_path, monkeypatch):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """models:
  defaults:
    model: openai/yaml-default
    api_base: http://yaml-default.local/v1
    timeout: 90
    max_retries: 3
  requirements:
    model: anthropic/requirements
    api_base: http://requirements.local
    api_key_env: TEST_REQUIREMENTS_SECRET
  code:
    model: openai/code
    api_key: code-literal-key
    timeout: 180
  fix:
    model: ollama/fix
    api_base: http://localhost:11434
    max_retries: 0
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TEST_REQUIREMENTS_SECRET=dotenv-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("DOC2RUN_AGENT_MODEL", "openai/environment-fallback")
    monkeypatch.setenv("DOC2RUN_AGENT_API_BASE", "http://environment.local/v1")

    try:
        settings = load_agent_model_settings(config_path)
    finally:
        os.environ.pop("TEST_REQUIREMENTS_SECRET", None)

    assert settings.requirements.model == "anthropic/requirements"
    assert settings.requirements.api_base == "http://requirements.local"
    assert settings.requirements.api_key == "dotenv-secret"
    assert settings.requirements.timeout_seconds == 90
    assert settings.code.model == "openai/code"
    assert settings.code.api_base == "http://yaml-default.local/v1"
    assert settings.code.api_key == "code-literal-key"
    assert settings.code.timeout_seconds == 180
    assert settings.fix.model == "ollama/fix"
    assert settings.fix.api_base == "http://localhost:11434"
    assert settings.fix.max_retries == 0


def test_default_config_is_discovered_in_current_directory(tmp_path, monkeypatch):
    (tmp_path / "doc2run_agent.yaml").write_text(
        """models:
  defaults:
    model: openai/default
    api_key_env: TEST_SHARED_SECRET
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("TEST_SHARED_SECRET=shared-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    try:
        settings = load_agent_model_settings()
    finally:
        os.environ.pop("TEST_SHARED_SECRET", None)

    assert settings.requirements.api_key == "shared-secret"
    assert settings.code.api_key == "shared-secret"
    assert settings.fix.api_key == "shared-secret"


def test_missing_explicit_config_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_agent_model_settings(tmp_path / "missing.yaml")


def test_unknown_yaml_field_is_rejected(tmp_path):
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(
        "models:\n  defaults:\n    model: openai/test\n    surprise: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="surprise"):
        load_agent_model_settings(config_path)


def test_missing_api_key_environment_reference_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_MISSING_SECRET", raising=False)
    config_path = tmp_path / "missing-secret.yaml"
    config_path.write_text(
        """models:
  defaults:
    model: openai/test
    api_key_env: TEST_MISSING_SECRET
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="TEST_MISSING_SECRET"):
        load_agent_model_settings(config_path)
