from __future__ import annotations

import os
from pathlib import Path

import pytest

from git_workspace_mcp.security import (
    GitWorkspaceSecurityError,
    redact_secrets,
    resolve_repo,
    resolve_root,
    validate_ref,
    validate_relative_path,
)


def test_resolve_root_from_env(workspace_root: Path) -> None:
    assert resolve_root(None) == workspace_root


def test_resolve_root_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        resolve_root(str(tmp_path / "does-not-exist"))


def test_resolve_repo_accepts_known_repo(workspace_root: Path) -> None:
    repo = resolve_repo(workspace_root, "alpha")
    assert repo == workspace_root / "alpha"


def test_resolve_repo_rejects_non_repo(workspace_root: Path) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        resolve_repo(workspace_root, "not_a_repo")


def test_resolve_repo_rejects_traversal(workspace_root: Path) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        resolve_repo(workspace_root, "../etc")


def test_resolve_repo_rejects_symlink_escape(workspace_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace_root / "escape"
    link.symlink_to(outside)
    with pytest.raises(GitWorkspaceSecurityError):
        resolve_repo(workspace_root, "escape")


@pytest.mark.parametrize("ref", ["HEAD", "HEAD~1", "HEAD^^", "main", "v1.2.3", "abc123def", "main..feature", "HEAD~3..HEAD"])
def test_validate_ref_accepts_valid_refs(ref: str) -> None:
    assert validate_ref(ref) == ref


@pytest.mark.parametrize("ref", ["HEAD; rm -rf /", "main && curl evil", "HEAD\nrm", "main | cat", ""])
def test_validate_ref_rejects_shell_metachars(ref: str) -> None:
    with pytest.raises(GitWorkspaceSecurityError):
        validate_ref(ref)


def test_validate_relative_path_accepts_nested(workspace_root: Path) -> None:
    repo = workspace_root / "alpha"
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("ok\n")
    resolved = validate_relative_path(repo, "docs/guide.md")
    assert resolved.relative_to(repo) == Path("docs/guide.md")


@pytest.mark.parametrize("bad", ["../etc/passwd", "../../something", "./foo", "/abs/path", "docs/../../etc"])
def test_validate_relative_path_rejects_traversal(workspace_root: Path, bad: str) -> None:
    repo = workspace_root / "alpha"
    with pytest.raises(GitWorkspaceSecurityError):
        validate_relative_path(repo, bad)


def test_redact_secrets_masks_known_patterns() -> None:
    body = "token sk-abcdefghij and ghp_zzzzzzzzzz and xoxb-1234567890 plus password=swordfish"
    redacted = redact_secrets(body)
    assert "sk-abcdefghij" not in redacted
    assert "ghp_zzzzzzzzzz" not in redacted
    assert "xoxb-1234567890" not in redacted
    assert "swordfish" not in redacted
    assert "***REDACTED***" in redacted


def test_redact_secrets_bypass_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_WORKSPACE_REDACT_SECRETS", "false")
    body = "sk-abcdefghij"
    assert redact_secrets(body) == body
