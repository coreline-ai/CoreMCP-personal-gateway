# Git Workspace MCP

Read-only MCP server for git repositories under `/Users/hwanchoi/projects`.

It exposes seven `git`-only inspection tools. The server never writes, fetches,
or pushes — only the subcommands in `ALLOWED_SUBCOMMANDS` may be executed, all
argv tokens pass a strict safe-character allowlist, and diff/log/blame output is
scrubbed by the same secret redactor used by Project Docs MCP.

## Tools

- `repo_list` — list git repositories under `GIT_WORKSPACE_ROOT` with branch,
  dirty flag, ahead/behind, and last commit timestamp.
- `repo_status` — branch + untracked / staged / modified file lists for one repo.
- `repo_log` — recent commits (`limit` ≤ 200) with optional `since` / `author`,
  subject/body are passed through the secret redactor, file counts per commit.
- `repo_branch_list` — local branches (optional `include_remote=true`) with HEAD
  sha, last commit timestamp, and upstream tracking ref.
- `repo_diff` — `git diff <ref>` text + short stats with a 64 KB default body
  cap (max 1 MB) and secret redaction.
- `repo_blame` — `git blame --line-porcelain` with optional line range and a
  hard cap at 1000 lines.
- `repo_recent_activity` — per-repo aggregate (commits, files_changed,
  dominant author, last commit) within the last N days (1 ≤ N ≤ 365).

All tools advertise `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, `openWorldHint=false`.

## Security policy

- `GIT_WORKSPACE_ROOT` must be a real directory; the resolved path is symlink-
  collapsed and used as the only allowed parent for every repository.
- Repository names are validated as a single path segment — no `..`, no `/`,
  no leading dots — and must resolve under the workspace root.
- File paths are resolved relative to the repository root and rejected on
  symlink escape.
- Refs are matched against `^[A-Za-z0-9._/@\-]+(\.\.\.?[A-Za-z0-9._/@\-]+)?$`
  to allow `HEAD~1..HEAD` ranges but reject option injection.
- `ALLOWED_SUBCOMMANDS = {status, log, diff, branch, blame, show, rev-parse,
  rev-list, for-each-ref}` — anything else (push, fetch, commit, checkout, …)
  is refused before `subprocess` is invoked.
- argv tokens are matched against
  `^[A-Za-z0-9._:,=/\-\^~%@()' \x1e\x1f]+$`. Newlines, NUL bytes,
  `--upload-pack=` / `--receive-pack=` payloads are hard-blocked.
- `git -C <repo> --no-pager <sub> *args` runs under a 10 s timeout per call and
  output is capped at 1 MB by default (diff body has its own 64 KB cap).
- Diff bodies, log bodies, blame summaries, and blame line text are scrubbed by
  `redact_secrets()` (sk-/ghp_/xox*/JWT/`password=`/PEM markers).

## Runtime

```bash
GIT_WORKSPACE_ROOT=/Users/hwanchoi/projects python3 -m git_workspace_mcp.main
```

## Tests

```bash
cd apps/git-workspace-mcp && uv run pytest
```

## CoreMCP registration

From the CoreMCP repository root:

```bash
make git-workspace-register
make codex-smoke
infra/scripts/codex-exec-coremcp.sh "git_workspace.repo_recent_activity 도구로 최근 7일간 활동 정리해줘"
```

The script creates or updates a `git_workspace` stdio service and adds it to
the default toolbox. Exposed tools are namespaced as:

- `git_workspace.repo_list`
- `git_workspace.repo_status`
- `git_workspace.repo_log`
- `git_workspace.repo_branch_list`
- `git_workspace.repo_diff`
- `git_workspace.repo_blame`
- `git_workspace.repo_recent_activity`
