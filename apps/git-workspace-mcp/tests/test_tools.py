from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git_workspace_mcp.security import GitWorkspaceSecurityError
from git_workspace_mcp.tools import (
    repo_blame,
    repo_branch_list,
    repo_diff,
    repo_list,
    repo_log,
    repo_recent_activity,
    repo_status,
)


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env)
    return proc.stdout


async def test_repo_list_finds_two_repos(workspace_root: Path) -> None:
    payload = await repo_list(workspace_root)
    names = sorted(item["name"] for item in payload["items"])
    assert names == ["alpha", "beta"]
    for item in payload["items"]:
        assert item["branch"] == "main"
        assert item["dirty"] is False


async def test_repo_list_pattern_filter(workspace_root: Path) -> None:
    payload = await repo_list(workspace_root, pattern="alp")
    assert payload["count"] == 1
    assert payload["items"][0]["name"] == "alpha"


async def test_repo_status_clean(workspace_root: Path) -> None:
    payload = await repo_status(workspace_root, name="alpha")
    assert payload["dirty"] is False
    assert payload["branch"] == "main"


async def test_repo_status_dirty_after_edit(workspace_root: Path, alpha_repo: Path) -> None:
    (alpha_repo / "README.md").write_text("# repo\n\nedited\n")
    payload = await repo_status(workspace_root, name="alpha")
    assert payload["dirty"] is True
    assert "README.md" in payload["modified"]


async def test_repo_log_returns_initial_commit(workspace_root: Path) -> None:
    payload = await repo_log(workspace_root, name="alpha", limit=5)
    assert payload["count"] == 1
    assert payload["items"][0]["subject"] == "initial commit"


async def test_repo_log_limit_capped(workspace_root: Path) -> None:
    payload = await repo_log(workspace_root, name="alpha", limit=9999)
    assert payload["limit"] <= 200


async def test_repo_branch_list_local(workspace_root: Path, alpha_repo: Path) -> None:
    _git(alpha_repo, "branch", "feature/x")
    payload = await repo_branch_list(workspace_root, name="alpha")
    names = sorted(item["name"] for item in payload["items"])
    assert "main" in names
    assert "feature/x" in names


async def test_repo_diff_redacts_secrets(workspace_root: Path, beta_repo: Path) -> None:
    (beta_repo / "secrets.txt").write_text("token: sk-newvalue999999999\n")
    _git(beta_repo, "add", ".")
    _git(beta_repo, "commit", "-m", "rotate secret")
    payload = await repo_diff(workspace_root, name="beta", ref="HEAD~1..HEAD")
    assert "sk-newvalue999999999" not in payload["diff_text"]
    assert "***REDACTED***" in payload["diff_text"]
    assert payload["stat"]["files"] >= 1


async def test_repo_diff_truncate_bytes(workspace_root: Path, alpha_repo: Path) -> None:
    big_text = "x" * 10_000 + "\n"
    (alpha_repo / "big.txt").write_text(big_text)
    _git(alpha_repo, "add", ".")
    _git(alpha_repo, "commit", "-m", "add big file")
    payload = await repo_diff(workspace_root, name="alpha", ref="HEAD~1..HEAD", truncate_bytes=2048)
    assert payload["truncated"] is True
    assert len(payload["diff_text"].encode("utf-8")) <= 2048


async def test_repo_diff_rejects_path_traversal(workspace_root: Path) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        await repo_diff(workspace_root, name="alpha", paths=["../etc/passwd"])


async def test_repo_blame_lists_lines(workspace_root: Path, alpha_repo: Path) -> None:
    payload = await repo_blame(workspace_root, name="alpha", path="README.md")
    assert payload["count"] >= 1
    assert all("sha" in item for item in payload["items"])


async def test_repo_blame_line_range(workspace_root: Path, alpha_repo: Path) -> None:
    (alpha_repo / "multi.md").write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")
    _git(alpha_repo, "add", ".")
    _git(alpha_repo, "commit", "-m", "multi line")
    payload = await repo_blame(workspace_root, name="alpha", path="multi.md", line_start=3, line_end=7)
    assert payload["count"] == 5


async def test_repo_recent_activity_includes_commits(workspace_root: Path) -> None:
    payload = await repo_recent_activity(workspace_root, days=30)
    repos = sorted(item["repo"] for item in payload["items"])
    assert "alpha" in repos
    for item in payload["items"]:
        assert item["commits"] >= 1
