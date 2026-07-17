from __future__ import annotations

import asyncio
import os
from typing import Any

from mini_agent.tools.builtin.common import resolve_workspace_path
from mini_agent.tools.types import ToolExecutionContext


SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _safe_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        process.kill()
    await process.wait()


async def powershell_exec(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    command = str(arguments["command"])
    cwd = resolve_workspace_path(
        context.workspace_root, str(arguments.get("cwd", ".")), must_exist=True
    )
    if not cwd.is_dir():
        raise NotADirectoryError(cwd)
    timeout = float(arguments.get("timeout_seconds", 120))
    process = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        command,
        cwd=str(cwd),
        env=_safe_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate_process_tree(process)
        raise TimeoutError(f"PowerShell command timed out after {timeout:g}s")
    output = stdout.decode("utf-8", errors="replace")
    error = stderr.decode("utf-8", errors="replace")
    rendered = (
        f"exit_code={process.returncode}\n"
        f"stdout:\n{output}\n"
        f"stderr:\n{error}"
    )
    if process.returncode != 0:
        raise RuntimeError(rendered)
    return rendered
