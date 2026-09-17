import argparse
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("server_update", Path(__file__).parents[1] / "scripts/server_update.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    updater.git(remote, "init", "-b", "deployment")
    updater.git(remote, "config", "user.email", "test@example.com")
    updater.git(remote, "config", "user.name", "test")
    (remote / ".gitignore").write_text("runtime/\ntmp/\ndata/\nconfig.json\n")
    (remote / "code.txt").write_text("old")
    updater.git(remote, "add", ".")
    updater.git(remote, "commit", "-m", "old")
    root = tmp_path / "installed"
    updater.run(["git", "clone", remote, root], cwd=tmp_path, capture=True)
    (root / "tmp/venv").mkdir(parents=True)
    (root / "tmp/venv/package.txt").write_text("old-package")
    (root / "data").mkdir()
    (root / "data/agent.db").write_text("session-data")
    (root / "config.json").write_text("private-config")
    (remote / "code.txt").write_text("new")
    updater.git(remote, "commit", "-am", "new")
    original = updater.run
    calls = []

    def fake_run(args, *, cwd, capture=False):
        args = [str(a) for a in args]
        if args[0] == "git":
            return original(args, cwd=cwd, capture=capture)
        calls.append(args)
        if "wheel" in args:
            wheels = Path(args[args.index("--wheel-dir") + 1])
            wheels.mkdir()
            (wheels / "mini_agent-0.2.0-py3-none-any.whl").touch()
        if "install" in args:
            (root / "tmp/venv/package.txt").write_text("new-package")
        return ""

    monkeypatch.setattr(updater, "run", fake_run)
    args = argparse.Namespace(mode=None, workspace=None, no_restart=True, check_only=False)
    return root, args, calls, fake_run


def test_update_preserves_data_and_snapshots_environment(deployment):
    root, args, calls, _ = deployment
    updated, _ = updater.update(root, args)
    assert updated
    assert (root / "code.txt").read_text() == "new"
    assert (root / "config.json").read_text() == "private-config"
    assert (root / "data/agent.db").read_text() == "session-data"
    backup = next((root / "runtime/updates").iterdir())
    assert (backup / "venv/package.txt").read_text() == "old-package"
    assert (backup / "data/agent.db").read_text() == "session-data"
    count = len(calls)
    assert updater.update(root, args)[0] is False
    assert len(calls) == count


def test_failed_install_restores_code_and_entire_environment(deployment, monkeypatch):
    root, args, _, fake_run = deployment
    old = updater.git(root, "rev-parse", "HEAD")

    def fail(args, **kwargs):
        result = fake_run(args, **kwargs)
        if "install" in args:
            (root / "tmp/venv/unwanted.txt").touch()
            raise RuntimeError("simulated installation failure")
        return result

    monkeypatch.setattr(updater, "run", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        updater.update(root, args)
    assert updater.git(root, "rev-parse", "HEAD") == old
    assert (root / "tmp/venv/package.txt").read_text() == "old-package"
    assert not (root / "tmp/venv/unwanted.txt").exists()
    assert (root / "data/agent.db").read_text() == "session-data"


def test_check_only_does_not_stop_or_install(deployment):
    root, args, calls, _ = deployment
    args.check_only = True
    args.no_restart = False
    assert updater.update(root, args)[0] is False
    assert not calls
    assert (root / "code.txt").read_text() == "old"


@pytest.mark.parametrize("check_only", [True, False])
def test_update_with_same_named_branch_and_tag(deployment, check_only):
    root, args, calls, _ = deployment
    updater.git(root, "config", "core.warnAmbiguousRefs", "true")
    updater.git(root, "tag", "deployment")
    assert updater.git(root, "symbolic-ref", "--quiet", "--short", "HEAD") == "heads/deployment"
    args.check_only = check_only
    updated, _ = updater.update(root, args)
    assert updated is (not check_only)
    assert (root / "code.txt").read_text() == ("old" if check_only else "new")
    if check_only:
        assert not calls


def test_first_use_installs_even_after_manual_pull(deployment):
    root, args, calls, _ = deployment
    updater.git(root, "pull", "--ff-only", "origin", "deployment")
    assert updater.update(root, args)[0] is True
    assert any("install" in command for command in calls)


def test_download_failure_does_not_stop_agent(deployment, monkeypatch):
    root, args, calls, fake_run = deployment

    def fail(args, **kwargs):
        if "wheel" in args:
            raise RuntimeError("download failed")
        return fake_run(args, **kwargs)

    monkeypatch.setattr(updater, "run", fail)
    with pytest.raises(RuntimeError, match="download failed"):
        updater.update(root, args)
    assert not calls
    assert (root / "code.txt").read_text() == "old"


def test_local_changes_block_update(deployment):
    root, args, calls, _ = deployment
    (root / "code.txt").write_text("user edit")
    with pytest.raises(RuntimeError, match="local changes"):
        updater.update(root, args)
    assert not calls


def test_restart_uses_saved_workspace_and_permissions(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(updater.subprocess, "call", lambda args, **kwargs: seen.append(args) or 0)
    updater.restart(tmp_path, {"mode": "cli", "workspace": "C:/work with spaces", "disable_shell": True, "permission_mode": "locked"})
    assert "C:/work with spaces" in seen[0]
    assert "-DisableShell" in seen[0]
    assert seen[0][-1] == "locked"
