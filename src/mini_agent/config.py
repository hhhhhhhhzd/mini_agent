from __future__ import annotations

import os
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from mini_agent.tools.permissions import PermissionMode


FIXED_MODEL_ID = "qwen3.7-plus"
FIXED_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_CONTEXT_WINDOW = 262_144


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration for the fixed-model agent."""

    api_key: str = field(repr=False)
    workspace_root: Path
    data_dir: Path
    model_id: str = FIXED_MODEL_ID
    base_url: str = FIXED_BASE_URL
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = 16_384
    request_timeout_seconds: float = 600.0
    shell_enabled: bool = False
    permission_mode: PermissionMode = PermissionMode.STANDARD

    @classmethod
    def load(
        cls,
        *,
        config_path: Path | None = None,
        workspace_root: Path | None = None,
        data_dir: Path | None = None,
    ) -> "AgentConfig":
        """Load model settings from the agent project's JSON configuration."""
        path = (config_path or Path(os.getenv("MINI_AGENT_CONFIG") or
                Path(__file__).resolve().parents[2] / "config.json")).resolve()
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            raise RuntimeError(f"Cannot read valid JSON configuration: {path}") from None
        if not isinstance(document, dict) or not isinstance(document.get("model"), dict):
            raise RuntimeError(f"Invalid configuration {path}: model must be an object")
        model = document["model"]

        def invalid(name: str) -> None:
            raise RuntimeError(f"Invalid configuration {path}: model.{name}")

        for name in ("api_key", "model_id", "base_url"):
            if not isinstance(model.get(name), str) or not model[name].strip():
                invalid(name)
        try:
            url = urlsplit(model["base_url"])
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                invalid("base_url")
        except ValueError:
            invalid("base_url")
        numbers = {
            "context_window": model.get("context_window", DEFAULT_CONTEXT_WINDOW),
            "max_output_tokens": model.get("max_output_tokens", 16384),
            "request_timeout_seconds": model.get("request_timeout_seconds", 600.0),
        }
        for name, value in numbers.items():
            allowed = (int, float) if name == "request_timeout_seconds" else (int,)
            if type(value) not in allowed or value <= 0 or (isinstance(value, float) and not math.isfinite(value)):
                invalid(name)
        if numbers["max_output_tokens"] >= numbers["context_window"]:
            invalid("max_output_tokens")
        permission_value = os.getenv("MINI_AGENT_PERMISSION_MODE", "standard").lower()
        try:
            permission_mode = PermissionMode(permission_value)
        except ValueError:
            raise RuntimeError("Invalid MINI_AGENT_PERMISSION_MODE") from None
        return cls(
            api_key=model["api_key"].strip(),
            model_id=model["model_id"].strip(),
            base_url=model["base_url"].strip().rstrip("/"),
            workspace_root=(workspace_root or Path.cwd()).resolve(),
            data_dir=cls.resolve_data_dir(data_dir),
            permission_mode=permission_mode,
            shell_enabled=os.getenv("MINI_AGENT_ENABLE_SHELL", "0").lower() in {"1", "true", "yes"},
            **numbers,
        )

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
