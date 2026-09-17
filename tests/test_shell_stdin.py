from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell stdin inheritance")
@pytest.mark.parametrize("command,expected", [
    ("[Console]::OpenStandardInput().ReadByte()", "-1"),
    ("git --version; exit $LASTEXITCODE", "git version"),
])
def test_shell_does_not_inherit_open_host_stdin(tmp_path: Path, command: str, expected: str) -> None:
    if command.startswith("git") and not shutil.which("git"):
        pytest.skip("Git is not installed")
    # Keep the host pipe open and empty, as it would be while awaiting ACP messages.
    host_code = """
import asyncio
import sys
from pathlib import Path
from mini_agent.tools.builtin.shell.powershell import powershell_exec
from mini_agent.tools.types import ToolExecutionContext
result = asyncio.run(powershell_exec(
    {"command": sys.argv[2], "timeout_seconds": 4},
    ToolExecutionContext("stdin-regression", Path(sys.argv[1])),
))
print(result)
"""
    with subprocess.Popen(
        [sys.executable, "-c", host_code, str(tmp_path), command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as host:
        try:
            # communicate() would close stdin and hide the original regression.
            host.wait(timeout=15)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/PID", str(host.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
            )
            raise
        finally:
            stdout, stderr = host.communicate(timeout=10)
    assert host.returncode == 0, stderr
    assert "exit_code=0" in stdout
    assert expected in stdout
