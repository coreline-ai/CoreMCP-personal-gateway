"""Tool implementations — 7 read-only git tools.

Each tool returns a JSON-serializable dict that becomes
`tools/call` `structuredContent`.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from .git_runner import GitRunError, run_git
from .security import (
    GitWorkspaceSecurityError,
    redact_secrets,
    resolve_repo,
    validate_ref,
    validate_relative_path,
)

DIFF_DEFAULT_TRUNCATE_BYTES = 65_536
LOG_MAX_LIMIT = 200
BLAME_MAX_LINES = 1000


async def _repo_branch(repo: Path) -> str:
    try:
        result = await run_git(repo, "rev-parse", "--abbrev-ref", "HEAD", timeout_seconds=5.0)
        return result.stdout.strip()
    except GitRunError:
        return "HEAD"


async def _repo_dirty(repo: Path) -> bool:
    try:
        result = await run_git(repo, "status", "--porcelain", timeout_seconds=5.0)
        return bool(result.stdout.strip())
    except GitRunError:
        return False


async def _repo_ahead_behind(repo: Path) -> tuple[int, int]:
    try:
        result = await run_git(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}", timeout_seconds=5.0)
        parts = result.stdout.split()
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except (GitRunError, ValueError):
        pass
    return 0, 0


async def _repo_last_commit_at(repo: Path) -> str | None:
    try:
        result = await run_git(repo, "log", "-1", "--format=%cI", timeout_seconds=5.0)
        value = result.stdout.strip()
        return value or None
    except GitRunError:
        return None


async def _summarize_repo(repo: Path, name: str) -> dict[str, Any]:
    branch, dirty, ahead_behind, last_commit_at = await asyncio.gather(
        _repo_branch(repo),
        _repo_dirty(repo),
        _repo_ahead_behind(repo),
        _repo_last_commit_at(repo),
    )
    ahead, behind = ahead_behind
    return {
        "name": name,
        "path": str(repo),
        "branch": branch,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "last_commit_at": last_commit_at,
    }


async def repo_list(root: Path, *, pattern: str | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / ".git").exists():
            continue
        if pattern and pattern.lower() not in entry.name.lower():
            continue
        items.append(await _summarize_repo(entry.resolve(), entry.name))
    return {"root": str(root), "count": len(items), "items": items}


async def repo_status(root: Path, *, name: str) -> dict[str, Any]:
    repo = resolve_repo(root, name)
    branch = await _repo_branch(repo)
    porcelain = await run_git(repo, "status", "--porcelain=v1", timeout_seconds=5.0)
    untracked: list[str] = []
    staged: list[str] = []
    modified: list[str] = []
    for line in porcelain.stdout.splitlines():
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:].strip().strip('"')
        if code == "??":
            untracked.append(path)
            continue
        if code[0] not in {" ", "?"}:
            staged.append(path)
        if code[1] not in {" ", "?"}:
            modified.append(path)
    dirty = bool(untracked or staged or modified)
    return {
        "name": name,
        "branch": branch,
        "dirty": dirty,
        "untracked": untracked,
        "staged": staged,
        "modified": modified,
    }


def _parse_log(stdout: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    record_separator = "\x1e"
    field_separator = "\x1f"
    for record in stdout.split(record_separator):
        record = record.strip("\n")
        if not record:
            continue
        head, _, numstat = record.partition("\n")
        fields = head.split(field_separator)
        if len(fields) < 5:
            continue
        sha, author, date, subject, body = fields[:5]
        files_changed = 0
        for stat_line in numstat.splitlines():
            stat_line = stat_line.strip()
            if not stat_line:
                continue
            files_changed += 1
        entries.append(
            {
                "sha": sha,
                "author": author,
                "date": date,
                "subject": subject,
                "body": redact_secrets(body),
                "files_changed_count": files_changed,
            }
        )
    return entries


async def repo_log(
    root: Path,
    *,
    name: str,
    limit: int = 20,
    since: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    repo = resolve_repo(root, name)
    capped_limit = max(1, min(int(limit), LOG_MAX_LIMIT))
    format_spec = "--format=\x1e%H\x1f%an\x1f%cI\x1f%s\x1f%b"
    argv: list[str] = [format_spec, "--numstat", f"-n{capped_limit}"]
    if since:
        argv.append(f"--since={since}")
    if author:
        argv.append(f"--author={author}")
    result = await run_git(repo, "log", *argv)
    entries = _parse_log(result.stdout)
    return {
        "name": name,
        "limit": capped_limit,
        "since": since,
        "author": author,
        "count": len(entries),
        "items": entries,
        "truncated": result.truncated,
    }


async def repo_branch_list(root: Path, *, name: str, include_remote: bool = False) -> dict[str, Any]:
    repo = resolve_repo(root, name)
    refspecs = ["refs/heads"]
    if include_remote:
        refspecs.append("refs/remotes/origin")
    fmt = "%(refname:short)\x1f%(objectname)\x1f%(committerdate:iso8601-strict)\x1f%(upstream:short)"
    result = await run_git(repo, "for-each-ref", f"--format={fmt}", *refspecs)
    items: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        items.append(
            {
                "name": parts[0],
                "head_sha": parts[1],
                "last_commit_at": parts[2],
                "remote_tracking": parts[3] if len(parts) >= 4 and parts[3] else None,
            }
        )
    return {"name": name, "include_remote": include_remote, "count": len(items), "items": items}


_DIFF_STAT_PATTERN = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


async def repo_diff(
    root: Path,
    *,
    name: str,
    ref: str = "HEAD",
    paths: list[str] | None = None,
    truncate_bytes: int = DIFF_DEFAULT_TRUNCATE_BYTES,
) -> dict[str, Any]:
    repo = resolve_repo(root, name)
    cleaned_ref = validate_ref(ref)
    resolved_paths: list[str] = []
    if paths:
        for raw_path in paths:
            resolved = validate_relative_path(repo, raw_path)
            resolved_paths.append(str(resolved.relative_to(repo)))
    cap = max(1024, min(int(truncate_bytes), 1_000_000))

    stat_argv: list[str] = ["--shortstat", cleaned_ref]
    if resolved_paths:
        stat_argv.append("--")
        stat_argv.extend(resolved_paths)
    stat_result = await run_git(repo, "diff", *stat_argv)
    files = insertions = deletions = 0
    match = _DIFF_STAT_PATTERN.search(stat_result.stdout)
    if match:
        files = int(match.group(1) or 0)
        insertions = int(match.group(2) or 0)
        deletions = int(match.group(3) or 0)

    body_argv: list[str] = [cleaned_ref]
    if resolved_paths:
        body_argv.append("--")
        body_argv.extend(resolved_paths)
    body_result = await run_git(repo, "diff", *body_argv, max_output_bytes=cap)
    return {
        "name": name,
        "ref": cleaned_ref,
        "paths": resolved_paths,
        "stat": {"files": files, "insertions": insertions, "deletions": deletions},
        "truncated": body_result.truncated,
        "diff_text": redact_secrets(body_result.stdout),
    }


async def repo_blame(
    root: Path,
    *,
    name: str,
    path: str,
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict[str, Any]:
    repo = resolve_repo(root, name)
    resolved = validate_relative_path(repo, path)
    relative = str(resolved.relative_to(repo))
    argv: list[str] = ["--line-porcelain"]
    if line_start is not None and line_end is not None:
        if line_start < 1 or line_end < line_start:
            raise GitWorkspaceSecurityError("invalid line range")
        argv.extend(["-L", f"{int(line_start)},{int(line_end)}"])
    argv.extend(["--", relative])
    result = await run_git(repo, "blame", *argv)
    lines: list[dict[str, Any]] = []
    pending: dict[str, Any] = {}
    current_line_number: int | None = None
    for raw_line in result.stdout.splitlines():
        if not raw_line:
            continue
        if raw_line[0] == "\t":
            if pending and current_line_number is not None:
                pending["text"] = redact_secrets(raw_line[1:])
                pending["line"] = current_line_number
                lines.append(pending)
                if len(lines) >= BLAME_MAX_LINES:
                    break
                pending = {}
            continue
        if not pending:
            sha_token, *rest = raw_line.split(" ")
            current_line_number = int(rest[1]) if len(rest) >= 2 and rest[1].isdigit() else None
            pending = {"sha": sha_token}
            continue
        head, _, tail = raw_line.partition(" ")
        if head == "author":
            pending["author"] = tail
        elif head == "author-time":
            pending["author_time"] = tail
        elif head == "summary":
            pending["summary"] = redact_secrets(tail)
    truncated = len(lines) >= BLAME_MAX_LINES
    return {"name": name, "path": relative, "count": len(lines), "items": lines, "truncated": truncated}


async def repo_recent_activity(root: Path, *, days: int = 7) -> dict[str, Any]:
    capped_days = max(1, min(int(days), 365))
    since = f"{capped_days} days ago"
    items: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not (entry / ".git").exists():
            continue
        try:
            log_result = await run_git(
                entry.resolve(),
                "log",
                f"--since={since}",
                "--pretty=format:%H\x1f%an",
                "--numstat",
            )
        except GitRunError:
            continue
        commits = 0
        files_changed = 0
        authors: dict[str, int] = {}
        last_commit_at: str | None = None
        for record in log_result.stdout.split("\n\n"):
            record = record.strip()
            if not record:
                continue
            head_line, _, numstat = record.partition("\n")
            if "\x1f" not in head_line:
                continue
            _, _, author = head_line.partition("\x1f")
            commits += 1
            authors[author] = authors.get(author, 0) + 1
            for stat_line in numstat.splitlines():
                if stat_line.strip():
                    files_changed += 1
        if commits == 0:
            continue
        try:
            last = await run_git(
                entry.resolve(),
                "log",
                "-1",
                "--format=%cI",
                f"--since={since}",
                timeout_seconds=5.0,
            )
            last_commit_at = last.stdout.strip() or None
        except GitRunError:
            last_commit_at = None
        dominant = max(authors.items(), key=lambda x: x[1])[0] if authors else None
        items.append(
            {
                "repo": entry.name,
                "commits": commits,
                "files_changed": files_changed,
                "last_commit_at": last_commit_at,
                "dominant_author": dominant,
            }
        )
    items.sort(key=lambda x: x["commits"], reverse=True)
    return {"days": capped_days, "count": len(items), "items": items}
