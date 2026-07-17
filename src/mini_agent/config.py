from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mini_agent.tools.permissions import PermissionMode


FIXED_MODEL_ID = "qwen3.7-plus"
FIXED_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_CONTEXT_WINDOW = 262_144


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration for the fixed-model agent."""

    api_key: str
    workspace_root: Path
    data_dir: Path
    model_id: str = FIXED_MODEL_ID
    base_url: str = FIXED_BASE_URL
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = 16_384
    request_timeout_seconds: float = 600.0
    shell_enabled: bool = False
    permission_mode: PermissionMode = PermissionMode.STANDARD

    @staticmethod
    def resolve_data_dir(data_dir: Path | None = None) -> Path:
        default_data = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MiniAgent"
        env_data = os.getenv("MINI_AGENT_DATA_DIR")
        return (data_dir or (Path(env_data) if env_data else default_data)).resolve()

    @classmethod
    def from_env(
        cls,
        *,
        workspace_root: Path | None = None,
        data_dir: Path | None = None,
    ) -> "AgentConfig":
        api_key = os.getenv("MINI_AGENT_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Missing API key. Set MINI_AGENT_API_KEY (or DASHSCOPE_API_KEY)."
            )
        workspace = (workspace_root or Path.cwd()).resolve()
        permission_value = os.getenv("MINI_AGENT_PERMISSION_MODE", "standard").lower()
        try:
            permission_mode = PermissionMode(permission_value)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in PermissionMode)
            raise RuntimeError(
                f"Invalid MINI_AGENT_PERMISSION_MODE={permission_value!r}; expected one of: {allowed}"
            ) from exc
        return cls(
            api_key=api_key,
            workspace_root=workspace,
            data_dir=cls.resolve_data_dir(data_dir),
            context_window=int(
                os.getenv("MINI_AGENT_CONTEXT_WINDOW", str(DEFAULT_CONTEXT_WINDOW))
            ),
            max_output_tokens=int(os.getenv("MINI_AGENT_MAX_OUTPUT_TOKENS", "16384")),
            shell_enabled=os.getenv("MINI_AGENT_ENABLE_SHELL", "0").lower()
            in {"1", "true", "yes"},
            permission_mode=permission_mode,
        )
