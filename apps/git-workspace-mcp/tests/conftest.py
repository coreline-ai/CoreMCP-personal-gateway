from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _init_repo(path: Path, *, with_secret: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test Author")
    (path / "README.md").write_text("# repo\n\ninitial content\n")
    if with_secret:
        (path / "secrets.txt").write_text("token: sk-abcdef0123456789\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial commit\n\nsubject body line")
    return path


@pytest.fixture
def workspace_root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "projects"
    root.mkdir()
    _init_repo(root / "alpha")
    _init_repo(root / "beta", with_secret=True)
    (root / "not_a_repo").mkdir()
    (root / "not_a_repo" / "README.md").write_text("# not a git repo\n")
    prev = os.environ.get("GIT_WORKSPACE_ROOT")
    os.environ["GIT_WORKSPACE_ROOT"] = str(root)
    try:
        yield root
    finally:
        if prev is None:
            os.environ.pop("GIT_WORKSPACE_ROOT", None)
        else:
            os.environ["GIT_WORKSPACE_ROOT"] = prev


@pytest.fixture
def alpha_repo(workspace_root: Path) -> Path:
    return workspace_root / "alpha"


@pytest.fixture
def beta_repo(workspace_root: Path) -> Path:
    return workspace_root / "beta"
