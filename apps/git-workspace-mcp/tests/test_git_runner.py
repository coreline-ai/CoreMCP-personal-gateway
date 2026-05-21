from __future__ import annotations

from pathlib import Path

import pytest

from git_workspace_mcp.git_runner import GitRunError, run_git
from git_workspace_mcp.security import GitWorkspaceSecurityError


async def test_run_git_status_ok(alpha_repo: Path) -> None:
    result = await run_git(alpha_repo, "status", "--porcelain")
    assert result.returncode == 0
    assert result.stdout == ""


async def test_run_git_rejects_disallowed_subcommand(alpha_repo: Path) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        await run_git(alpha_repo, "push")
    with pytest.raises(GitWorkspaceSecurityError):
        await run_git(alpha_repo, "commit", "-m", "no")
    with pytest.raises(GitWorkspaceSecurityError):
        await run_git(alpha_repo, "checkout", "main")


async def test_run_git_rejects_unsafe_argv(alpha_repo: Path) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        await run_git(alpha_repo, "log", "--upload-pack=evil")
    with pytest.raises(GitWorkspaceSecurityError):
        await run_git(alpha_repo, "log", "HEAD; rm")


async def test_run_git_truncates(alpha_repo: Path) -> None:
    result = await run_git(alpha_repo, "log", "--format=%H", max_output_bytes=4)
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 4


async def test_run_git_non_zero_raises(alpha_repo: Path) -> None:
    with pytest.raises(GitRunError):
        await run_git(alpha_repo, "log", "this-ref-does-not-exist")
