"""Update a Windows source deployment, preserving its data and launch settings."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile


def run(args, *, cwd, capture=False):
    return subprocess.run(
        [str(arg) for arg in args], cwd=cwd, stdin=subprocess.DEVNULL,
        check=True, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def git(root, *args):
    return run(["git", "-C", root, *args], cwd=root, capture=True).strip()


@contextmanager
def update_lock(root):
    import msvcrt
    path = root / "runtime" / "update.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Another update is running.") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def launch_settings(root, args):
    path = root / "runtime" / "launch.json"
    settings = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if args.mode:
        settings["mode"] = args.mode
    if args.workspace:
        settings["workspace"] = str(Path(args.workspace).resolve())
    if not args.no_restart and not args.check_only and settings.get("mode") not in ("cli", "weixin"):
        raise RuntimeError("First update: specify -Mode cli or -Mode weixin (and -Workspace if needed).")
    settings.setdefault("workspace", str(root / "workspace"))
    if not args.no_restart and not args.check_only and not Path(settings["workspace"]).is_dir():
        raise RuntimeError("Saved workspace does not exist; specify -Workspace.")
    return settings


def restart(root, settings):
    mode = settings["mode"]
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            root / "scripts" / f"server-{mode}.ps1", "-Workspace", settings["workspace"]]
    if settings.get("disable_shell"):
        args.append("-DisableShell")
    if mode == "cli":
        args += ["-PermissionMode", settings.get("permission_mode", "trusted")]
    else:
        args += ["-WeixinAcpVersion", settings.get("weixin_acp_version", "0.6.0")]
    print("Starting Agent in this window; Ctrl+C stops it.", flush=True)
    # The CLI itself needs the console; only updater commands use DEVNULL.
    return subprocess.call([str(arg) for arg in args], cwd=root)


def restore_environment(root, backup):
    live = root / "tmp" / "venv"
    failed = backup / "failed-venv"
    # Both destinations are fixed children of the resolved installation/backup.
    if live.exists():
        live.rename(failed)
    shutil.copytree(backup / "venv", live)


def update(root, args):
    old = git(root, "rev-parse", "HEAD")
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeError("Working tree has local changes/untracked files. Preserve them before updating.")
    settings = launch_settings(root, args)
    print(f"Checking origin/{branch}...", flush=True)
    git(root, "fetch", "origin", f"refs/heads/{branch}")
    target = git(root, "rev-parse", "FETCH_HEAD")
    git(root, "merge-base", "--is-ancestor", old, target)
    state_path = root / "runtime" / "update-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if old == target and state.get("installed_commit") == target:
        print("Already up to date; Agent was not stopped.")
        return False, settings
    if old == target:
        print("Installing current commit: no matching installed-version record.", flush=True)
    print(f"Update: {old[:8]} -> {target[:8]}", flush=True)
    if args.check_only:
        return False, settings

    backup = root / "runtime" / "updates" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    print(f"Backup/staging: {backup}", flush=True)
    (backup / "version.json").write_text(json.dumps({"old": old, "target": target, "branch": branch}), encoding="utf-8")
    archive = backup / "source.zip"
    git(root, "archive", "--format=zip", f"--output={archive}", target)
    stage = backup / "source"
    with zipfile.ZipFile(archive) as source:
        for member in source.namelist():
            (stage / member).resolve().relative_to(stage.resolve())
        source.extractall(stage)
    python = root / "tmp" / "venv" / "Scripts" / "python.exe"
    wheels = backup / "wheels"
    # Resolve/build dependencies before downtime; installation afterwards is offline.
    run([python, "-m", "pip", "wheel", "--wheel-dir", wheels, stage], cwd=root)
    if git(root, "rev-parse", "HEAD") != old or git(root, "status", "--porcelain"):
        raise RuntimeError("Repository changed during preparation; Agent was not stopped.")
    run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         root / "scripts" / "server-stop.ps1", "-Mode", "all"], cwd=root)
    changed = False
    try:
        # Snapshot failures stop the update before code/environment changes.
        if (root / "data").exists():
            shutil.copytree(root / "data", backup / "data")
        if (root / "config.json").exists():
            shutil.copy2(root / "config.json", backup / "config.json")
        shutil.copytree(root / "tmp" / "venv", backup / "venv")
        git(root, "merge", "--ff-only", target)
        changed = True
        package = list(wheels.glob("mini_agent-*.whl"))
        if len(package) != 1:
            raise RuntimeError("Expected exactly one mini_agent wheel.")
        run([python, "-m", "pip", "install", "--no-index", "--find-links", wheels,
             "--force-reinstall", package[0]], cwd=root)
        run([python, "-m", "pip", "check"], cwd=root)
        run([python, "-c", "from pathlib import Path; from mini_agent.config import AgentConfig; "
             "from mini_agent.app.factory import build_application; "
             "from mini_agent.protocols.acp.server import async_main; "
             "AgentConfig.load(config_path=Path('config.json')); print('Configuration and imports OK')"], cwd=root)
        if settings.get("mode"):
            run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 root / "scripts" / f"server-{settings['mode']}.ps1",
                 "-Workspace", settings["workspace"], "-ValidateOnly"], cwd=root)
        launch_file = root / "runtime" / "launch.json"
        launch_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        state_path.write_text(json.dumps({"installed_commit": target}), encoding="utf-8")
    except BaseException:
        print(f"Update failed; restoring {old[:8]}. Backup: {backup}", flush=True)
        if changed:
            try:
                git(root, "reset", "--keep", old)
            finally:
                restore_environment(root, backup)
        print("Old version restored. Start it with the usual server launcher.", flush=True)
        raise
    print(f"Update successful: {target[:8]}. Data/config preserved.", flush=True)
    return True, settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("cli", "weixin"))
    parser.add_argument("--workspace")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        with update_lock(root):
            updated, settings = update(root, args)
        if updated and not args.no_restart:
            return restart(root, settings)
        return 0
    except Exception as exc:
        print(f"Update error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
