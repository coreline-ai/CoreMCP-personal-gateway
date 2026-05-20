from __future__ import annotations

from pathlib import Path

ALLOWED_MARKDOWN_SUFFIXES = {".md", ".markdown"}
EXCLUDED_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


class ProjectDocsSecurityError(ValueError):
    """Raised when a requested path escapes the configured read-only boundary."""


def resolve_root(raw_root: str | None) -> Path:
    root = Path(raw_root or "/Users/hwanchoi/projects").expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ProjectDocsSecurityError(f"PROJECT_DOCS_ROOT does not exist or is not a directory: {root}")
    return root


def is_markdown_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ALLOWED_MARKDOWN_SUFFIXES


def is_excluded_path(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return True
    return any(part in EXCLUDED_DIR_NAMES for part in rel.parts)


def safe_relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ProjectDocsSecurityError("path escapes PROJECT_DOCS_ROOT") from exc


def project_path(root: Path, project: str) -> Path:
    if not project or "/" in project or "\\" in project or project in {".", ".."}:
        raise ProjectDocsSecurityError("project must be a direct child directory name")
    resolved = (root / project).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectDocsSecurityError("project escapes PROJECT_DOCS_ROOT") from exc
    if not resolved.is_dir():
        raise ProjectDocsSecurityError(f"project not found: {project}")
    if is_excluded_path(resolved, root):
        raise ProjectDocsSecurityError("project is excluded")
    return resolved


def document_path(root: Path, project: str, relative_path: str) -> Path:
    base = project_path(root, project)
    if not relative_path or Path(relative_path).is_absolute():
        raise ProjectDocsSecurityError("path must be relative to the project")
    resolved = (base / relative_path).resolve()
    try:
        resolved.relative_to(base)
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectDocsSecurityError("document path escapes the project/root") from exc
    if is_excluded_path(resolved, root):
        raise ProjectDocsSecurityError("document path is excluded")
    if not is_markdown_file(resolved):
        raise ProjectDocsSecurityError("only .md/.markdown files are readable")
    return resolved
