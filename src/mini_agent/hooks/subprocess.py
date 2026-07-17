from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mini_agent.core.types import HookOutcome
from mini_agent.hooks.types import HookHandler


SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def filtered_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }


async def run_command_hook(
    handler: HookHandler,
    envelope: dict[str, Any],
    *,
    default_cwd: Path,
    max_output_chars: int = 65_536,
) -> HookOutcome:
    if not handler.command:
        return HookOutcome(blocked=handler.required, reason="Hook command is empty")

    try:
        process = await asyncio.create_subprocess_exec(
            *handler.command,
            cwd=str(handler.cwd or default_cwd),
            env=filtered_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        reason = f"Cannot start Hook command: {exc}"
        return HookOutcome(
            blocked=handler.required,
            reason=reason if handler.required else None,
            warnings=(reason,),
        )
    payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload), timeout=handler.timeout_seconds
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        reason = f"Hook timed out after {handler.timeout_seconds:g}s"
        return HookOutcome(blocked=handler.required, reason=reason, warnings=(reason,))

    stdout_text = stdout.decode("utf-8", errors="replace")[:max_output_chars].strip()
    stderr_text = stderr.decode("utf-8", errors="replace")[:max_output_chars].strip()
    if process.returncode != 0:
        reason = stderr_text or f"Hook exited with code {process.returncode}"
        return HookOutcome(blocked=handler.required, reason=reason, warnings=(reason,))
    if not stdout_text:
        return HookOutcome(warnings=((stderr_text,) if stderr_text else ()))

    try:
        result = json.loads(stdout_text)
    except json.JSONDecodeError:
        warning = "Hook stdout was not valid JSON"
        return HookOutcome(
            blocked=handler.required,
            reason=warning if handler.required else None,
            warnings=(warning,),
        )
    if not isinstance(result, dict):
        warning = "Hook JSON output must be an object"
        return HookOutcome(
            blocked=handler.required,
            reason=warning if handler.required else None,
            warnings=(warning,),
        )

    decision = str(result.get("decision", "continue")).lower()
    additional = result.get("additional_context")
    if isinstance(additional, str):
        additional_context = (additional,)
    elif isinstance(additional, list):
        additional_context = tuple(str(item) for item in additional)
    else:
        additional_context = ()
    rewritten = result.get("rewritten_arguments")
    return HookOutcome(
        blocked=decision == "block",
        reason=str(result.get("reason")) if result.get("reason") else None,
        additional_context=additional_context,
        rewritten_arguments=rewritten if isinstance(rewritten, dict) else None,
        warnings=tuple(
            item
            for item in (
                str(result.get("warning")) if result.get("warning") else "",
                stderr_text,
            )
            if item
        ),
    )
