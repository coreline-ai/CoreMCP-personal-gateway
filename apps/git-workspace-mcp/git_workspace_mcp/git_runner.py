"""Subprocess wrapper for git read-only commands.

Only commands in `ALLOWED_SUBCOMMANDS` may be executed. All argv arguments
must already pass through `security.py`; this module is the last line of
defense before `subprocess`.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from .security import GitWorkspaceSecurityError

ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "branch",
        "blame",
        "show",
        "rev-parse",
        "rev-list",
        "for-each-ref",
    }
)

# argv must not introduce option injection or shell metacharacters. Single
# leading hyphen options (e.g. `-L`, `-n10`) are allowed but `--exec=...` /
# `--upload-pack=...` style payload smuggling is not. We deliberately accept
# `%` (format spec) and the ASCII record/unit separators (\x1e, \x1f) so
# `--format=%H\x1f%an` style pretty formats pass through; line-feed and NUL
# remain explicitly forbidden below.
_SAFE_ARG_PATTERN = re.compile(r"^[A-Za-z0-9._:,=/\-\^~%@()' \x1e\x1f]+$")


class GitRunError(RuntimeError):
    """Non-zero git exit or transport failure."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {command[1] if len(command) > 1 else ''} failed (rc={returncode})")
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class GitRunResult:
    stdout: str
    truncated: bool
    returncode: int


def _validate_argv(argv: tuple[str, ...]) -> None:
    for token in argv:
        if not isinstance(token, str) or not token:
            raise GitWorkspaceSecurityError("argv tokens must be non-empty strings")
        # Hard-block option-with-value smuggling that targets git transports.
        if token.startswith("--upload-pack=") or token.startswith("--receive-pack="):
            raise GitWorkspaceSecurityError(f"unsafe git option: {token}")
        if "\x00" in token or "\n" in token:
            raise GitWorkspaceSecurityError(f"argv contains control character: {token!r}")
        if not _SAFE_ARG_PATTERN.match(token):
            raise GitWorkspaceSecurityError(f"argv token failed safe character set: {token!r}")


async def run_git(
    repo: Path,
    subcommand: str,
    *args: str,
    max_output_bytes: int = 1_000_000,
    timeout_seconds: float = 10.0,
) -> GitRunResult:
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise GitWorkspaceSecurityError(f"git subcommand not allowed: {subcommand}")
    _validate_argv(args)

    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        "--no-pager",
        subcommand,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise GitRunError(["git", subcommand, *args], -1, "timeout") from None

    if process.returncode != 0:
        raise GitRunError(["git", subcommand, *args], process.returncode or -1, stderr_bytes.decode("utf-8", errors="replace"))

    truncated = len(stdout_bytes) > max_output_bytes
    body_bytes = stdout_bytes[:max_output_bytes] if truncated else stdout_bytes
    return GitRunResult(
        stdout=body_bytes.decode("utf-8", errors="replace"),
        truncated=truncated,
        returncode=process.returncode or 0,
    )
