from __future__ import annotations

from pathlib import Path
import json

import pytest

from mini_agent.config import AgentConfig, FIXED_BASE_URL, FIXED_MODEL_ID
from mini_agent.tools.permissions import PermissionMode


def test_json_config_is_independent_of_workspace_and_old_key(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": {"model_id": "custom", "base_url": "https://example.com/v1", "api_key": "file-secret"}}))
    monkeypatch.setenv("MINI_AGENT_CONFIG", str(path))
    monkeypatch.setenv("MINI_AGENT_API_KEY", "old-secret")
    monkeypatch.chdir(tmp_path.parent)
    config = AgentConfig.load(workspace_root=tmp_path.parent)
    assert config.api_key == "file-secret"
    assert config.model_id == "custom"
    assert "file-secret" not in repr(config)
    other = tmp_path / "other.json"
    other.write_text(path.read_text().replace("custom", "explicit"))
    assert AgentConfig.load(config_path=other).model_id == "explicit"


@pytest.mark.parametrize("field,value", [("api_key", ""), ("model_id", None), ("base_url", "bad"), ("context_window", True), ("max_output_tokens", -1), ("request_timeout_seconds", float("inf"))])
def test_invalid_json_fields_do_not_expose_key(tmp_path, field, value):
    model = {"model_id": "custom", "base_url": "https://example.com/v1", "api_key": "private-secret"}
    model[field] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": model}))
    with pytest.raises(RuntimeError) as error:
        AgentConfig.load(config_path=path)
    assert field in str(error.value)
    assert "private-secret" not in str(error.value)


def test_missing_and_malformed_config(tmp_path):
    path = tmp_path / "config.json"
    with pytest.raises(RuntimeError, match="configuration"):
        AgentConfig.load(config_path=path)
    path.write_text('{"api_key": "private-secret",')
    with pytest.raises(RuntimeError) as error:
        AgentConfig.load(config_path=path)
    assert "private-secret" not in str(error.value)


def test_fixed_model_config_and_data_dir_without_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MINI_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert AgentConfig.resolve_data_dir(tmp_path) == tmp_path.resolve()
    with pytest.raises(RuntimeError, match="Missing API key"):
        AgentConfig.from_env(workspace_root=tmp_path, data_dir=tmp_path)

    monkeypatch.setenv("MINI_AGENT_API_KEY", "test-only")
    config = AgentConfig.from_env(workspace_root=tmp_path, data_dir=tmp_path)
    assert config.model_id == FIXED_MODEL_ID == "qwen3.7-plus"
    assert config.base_url == FIXED_BASE_URL
    assert config.permission_mode == PermissionMode.STANDARD


def test_permission_mode_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINI_AGENT_API_KEY", "test-only")
    monkeypatch.setenv("MINI_AGENT_PERMISSION_MODE", "trusted")
    config = AgentConfig.from_env(workspace_root=tmp_path, data_dir=tmp_path)
    assert config.permission_mode == PermissionMode.TRUSTED

    monkeypatch.setenv("MINI_AGENT_PERMISSION_MODE", "unknown")
    with pytest.raises(RuntimeError, match="Invalid MINI_AGENT_PERMISSION_MODE"):
        AgentConfig.from_env(workspace_root=tmp_path, data_dir=tmp_path)
