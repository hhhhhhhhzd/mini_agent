from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.config import AgentConfig, FIXED_BASE_URL, FIXED_MODEL_ID
from mini_agent.tools.permissions import PermissionMode


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
