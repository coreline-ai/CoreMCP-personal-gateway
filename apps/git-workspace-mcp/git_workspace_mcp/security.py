"""Security primitives — root resolve, path validation, secret redaction.

`git-workspace-mcp` is read-only by design. Every external input (repo name,
ref, path) must pass through these helpers before it can flow into a git
subprocess argv. The security boundary is:

- Repo root: a single base directory (default `~/projects`); the env var
  `GIT_WORKSPACE_ROOT` may override it.
- Repos: direct child directories of the root containing a `.git` entry.
- Refs/paths: tightly validated character set to forbid shell metacharacters
  and `--`-style git option injection.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_ROOT = Path.home() / "projects"

# Refs accepted by `repo_log/diff/blame`:
#   - branches and tags (alphanumerics, hyphen, underscore, slash, dot)
#   - sha hexes
#   - relative refs (HEAD, HEAD~3, HEAD^, HEAD^^)
#   - range syntax `a..b` and `a...b`
_REF_PATTERN = re.compile(r"^[A-Za-z0-9._/\-]+(?:\^{1,3}|~\d+)?(?:\.{2,3}[A-Za-z0-9._/\-]+(?:\^{1,3}|~\d+)?)?$")

# Repo names = direct child dirs only (no slashes, no traversal).
_REPO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._\-][A-Za-z0-9._\- ]*$")

# Paths inside a repo must be relative POSIX-style without `..` segments.
_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9._\-][A-Za-z0-9._\- ]*$")

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"xox[abposr]-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)password\s*=\s*['\"]?[^\s'\"]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class GitWorkspaceSecurityError(ValueError):
    """Raised when an input violates the security boundary."""


def resolve_root(override: str | None = None) -> Path:
    raw = override if override is not None else os.environ.get("GIT_WORKSPACE_ROOT")
    base = Path(raw).expanduser() if raw else DEFAULT_ROOT
    if not base.exists():
        raise GitWorkspaceSecurityError(f"workspace root does not exist: {base}")
    if not base.is_dir():
        raise GitWorkspaceSecurityError(f"workspace root is not a directory: {base}")
    return base.resolve()


def resolve_repo(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not _REPO_NAME_PATTERN.match(name):
        raise GitWorkspaceSecurityError(f"invalid repo name: {name!r}")
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GitWorkspaceSecurityError(f"repo path escapes workspace root: {name}") from exc
    if not candidate.is_dir():
        raise GitWorkspaceSecurityError(f"repo not found: {name}")
    if not (candidate / ".git").exists():
        raise GitWorkspaceSecurityError(f"not a git repo: {name}")
    return candidate


def validate_ref(ref: str) -> str:
    if not isinstance(ref, str):
        raise GitWorkspaceSecurityError(f"ref must be a string: {ref!r}")
    cleaned = ref.strip()
    if not cleaned:
        raise GitWorkspaceSecurityError("ref must be non-empty")
    if not _REF_PATTERN.match(cleaned):
        raise GitWorkspaceSecurityError(f"invalid ref: {ref!r}")
    return cleaned


def validate_relative_path(repo: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise GitWorkspaceSecurityError("path must be a non-empty string")
    if "\x00" in raw_path:
        raise GitWorkspaceSecurityError("path contains NUL")
    parts = raw_path.replace("\\", "/").split("/")
    if any(segment in {"", ".", ".."} for segment in parts):
        raise GitWorkspaceSecurityError(f"path traversal rejected: {raw_path!r}")
    for segment in parts:
        if not _PATH_SEGMENT_PATTERN.match(segment):
            raise GitWorkspaceSecurityError(f"invalid path segment: {segment!r}")
    candidate = (repo / "/".join(parts)).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise GitWorkspaceSecurityError(f"path escapes repo root: {raw_path}") from exc
    return candidate


def redact_secrets(text: str) -> str:
    if os.environ.get("GIT_WORKSPACE_REDACT_SECRETS", "true").lower() == "false":
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED***", redacted)
    return redacted
