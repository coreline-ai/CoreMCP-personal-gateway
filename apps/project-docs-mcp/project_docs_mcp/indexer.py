from __future__ import annotations

import re
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import EXCLUDED_DIR_NAMES, document_path, is_excluded_path, is_markdown_file, project_path, safe_relative_path

MAX_FILE_BYTES_DEFAULT = 512 * 1024
MAX_READ_CHARS_DEFAULT = 20_000
MAX_SEARCH_RESULTS_DEFAULT = 10
MAX_SEARCH_RESULTS_HARD = 50
MAX_DOCS_PER_PROJECT = 1_000
MAX_PROJECTS = 500
MAX_SEARCHED_DOCS = 500
MAX_SEARCH_DOCS_PER_PROJECT_ALL = 25


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    path: str
    readme_path: str | None
    md_count: int
    updated_at: float | None


def _should_skip_dir(path: Path) -> bool:
    return path.name in EXCLUDED_DIR_NAMES


def _iter_project_dirs(root: Path) -> list[Path]:
    projects = [path for path in root.iterdir() if path.is_dir() and not _should_skip_dir(path) and not is_excluded_path(path, root)]
    return sorted(projects, key=lambda item: item.name.lower())[:MAX_PROJECTS]


def _iter_markdown_files(base: Path, root: Path, *, limit: int = MAX_DOCS_PER_PROJECT) -> list[Path]:
    files: list[Path] = []
    # Use os.walk so excluded heavy directories (node_modules, .git, .venv,
    # build outputs) are pruned before traversal. Path.rglob() would still
    # descend into those trees and can turn a read-only docs search into a
    # multi-minute scan on real developer workspaces.
    for current, dir_names, file_names in os.walk(base):
        current_path = Path(current)
        if is_excluded_path(current_path, root):
            dir_names[:] = []
            continue
        dir_names[:] = [
            name
            for name in dir_names
            if name not in EXCLUDED_DIR_NAMES and not is_excluded_path(current_path / name, root)
        ]
        for file_name in file_names:
            if len(files) >= limit:
                break
            path = current_path / file_name
            if is_excluded_path(path, root):
                continue
            if is_markdown_file(path):
                files.append(path)
        if len(files) >= limit:
            break
    return sorted(files, key=lambda item: safe_relative_path(root, item).lower())


def _readme_for(project_dir: Path) -> Path | None:
    for name in ("README.md", "README.markdown", "readme.md", "Readme.md"):
        path = project_dir / name
        if is_markdown_file(path):
            return path
    return None


def _mtime(files: list[Path]) -> float | None:
    mtimes = []
    for path in files:
        try:
            mtimes.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def list_projects(root: Path, *, query: str | None = None) -> dict[str, Any]:
    normalized_query = query.lower().strip() if isinstance(query, str) and query.strip() else None
    items: list[dict[str, Any]] = []
    for project_dir in _iter_project_dirs(root):
        if normalized_query and normalized_query not in project_dir.name.lower():
            continue
        files = _iter_markdown_files(project_dir, root)
        readme = _readme_for(project_dir)
        info = ProjectInfo(
            name=project_dir.name,
            path=safe_relative_path(root, project_dir),
            readme_path=safe_relative_path(project_dir, readme) if readme else None,
            md_count=len(files),
            updated_at=_mtime(files),
        )
        items.append(info.__dict__)
    return {"root": str(root), "items": items, "count": len(items)}


def list_docs(root: Path, *, project: str, limit: int = 200) -> dict[str, Any]:
    base = project_path(root, project)
    bounded_limit = max(1, min(int(limit or 200), MAX_DOCS_PER_PROJECT))
    files = _iter_markdown_files(base, root, limit=bounded_limit)
    return {
        "project": project,
        "items": [
            {
                "path": safe_relative_path(base, path),
                "root_path": safe_relative_path(root, path),
                "bytes": _safe_size(path),
                "updated_at": _safe_mtime(path),
            }
            for path in files
        ],
        "count": len(files),
    }


def read_doc(
    root: Path,
    *,
    project: str,
    path: str,
    max_chars: int = MAX_READ_CHARS_DEFAULT,
    max_file_bytes: int = MAX_FILE_BYTES_DEFAULT,
) -> dict[str, Any]:
    doc = document_path(root, project, path)
    size = _safe_size(doc)
    if size > max_file_bytes:
        raise ValueError(f"document exceeds max_file_bytes: {size} > {max_file_bytes}")
    text = doc.read_text(encoding="utf-8", errors="replace")
    bounded_chars = max(1, min(int(max_chars or MAX_READ_CHARS_DEFAULT), 100_000))
    truncated = len(text) > bounded_chars
    return {
        "project": project,
        "path": safe_relative_path(project_path(root, project), doc),
        "root_path": safe_relative_path(root, doc),
        "bytes": size,
        "updated_at": _safe_mtime(doc),
        "truncated": truncated,
        "content": text[:bounded_chars],
    }


def search_docs(
    root: Path,
    *,
    query: str,
    project: str | None = None,
    limit: int = MAX_SEARCH_RESULTS_DEFAULT,
    context_chars: int = 180,
    max_file_bytes: int = MAX_FILE_BYTES_DEFAULT,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    bounded_limit = max(1, min(int(limit or MAX_SEARCH_RESULTS_DEFAULT), MAX_SEARCH_RESULTS_HARD))
    haystacks: list[tuple[str, Path, Path]] = []
    if project:
        base = project_path(root, project)
        haystacks = [(project, base, path) for path in _iter_markdown_files(base, root)]
    else:
        for project_dir in _iter_project_dirs(root):
            # Global searches must be interactive. Scan a small, representative
            # slice per project (README/top-level docs first via sorted paths)
            # instead of deep-reading every archived project file.
            haystacks.extend(
                (project_dir.name, project_dir, path)
                for path in _iter_markdown_files(project_dir, root, limit=MAX_SEARCH_DOCS_PER_PROJECT_ALL)
            )
            if len(haystacks) >= MAX_SEARCHED_DOCS:
                haystacks = haystacks[:MAX_SEARCHED_DOCS]
                break

    needle = query.lower()
    results: list[dict[str, Any]] = []
    for project_name, project_dir, path in haystacks:
        if len(results) >= bounded_limit:
            break
        if _safe_size(path) > max_file_bytes:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lower = text.lower()
        index = lower.find(needle)
        if index < 0:
            continue
        start = max(0, index - context_chars)
        end = min(len(text), index + len(query) + context_chars)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        results.append(
            {
                "project": project_name,
                "path": safe_relative_path(project_dir, path),
                "root_path": safe_relative_path(root, path),
                "snippet": snippet,
                "match_offset": index,
            }
        )
    return {"query": query, "items": results, "count": len(results)}


def summarize_project(root: Path, *, project: str, max_chars: int = 6_000) -> dict[str, Any]:
    base = project_path(root, project)
    readme = _readme_for(base)
    docs = _iter_markdown_files(base, root)
    if readme is None:
        return {
            "project": project,
            "readme_path": None,
            "md_count": len(docs),
            "title": project,
            "headings": [],
            "snippet": "README.md not found.",
        }
    content = read_doc(root, project=project, path=safe_relative_path(base, readme), max_chars=max_chars)["content"]
    headings = [line.strip("# ").strip() for line in content.splitlines() if line.startswith("#")][:12]
    title = headings[0] if headings else project
    paragraphs = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    snippet = "\n".join(paragraphs[:8])[:max_chars]
    return {
        "project": project,
        "readme_path": safe_relative_path(base, readme),
        "md_count": len(docs),
        "title": title,
        "headings": headings,
        "snippet": snippet,
    }


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
