from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_docs_mcp.indexer import list_docs, list_projects, read_doc, search_docs, summarize_project
from project_docs_mcp.security import ProjectDocsSecurityError, document_path, resolve_root
from project_docs_mcp.server import ProjectDocsMcpServer


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    alpha = root / "alpha"
    alpha.mkdir()
    (alpha / "README.md").write_text("# Alpha\n\nCoreMCP alpha docs.\n\n## Usage\n", encoding="utf-8")
    (alpha / "notes.md").write_text("# Notes\n\nOAuth and Tailscale notes.\n", encoding="utf-8")
    (alpha / "secret.txt").write_text("not exposed", encoding="utf-8")
    beta = root / "beta"
    beta.mkdir()
    (beta / "README.markdown").write_text("# Beta\n\nBrowser QA project.\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "README.md").write_text("# ignored", encoding="utf-8")
    return root


def test_list_projects_and_docs(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    projects = list_projects(root)
    assert projects["count"] == 2
    assert projects["items"][0]["name"] == "alpha"
    assert projects["items"][0]["readme_path"] == "README.md"
    docs = list_docs(root, project="alpha")
    assert sorted(item["path"] for item in docs["items"]) == ["README.md", "notes.md"]


def test_read_and_search_docs(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    doc = read_doc(root, project="alpha", path="README.md", max_chars=8)
    assert doc["content"] == "# Alpha\n"
    assert doc["truncated"] is True
    results = search_docs(root, query="tailscale")
    assert results["count"] == 1
    assert results["items"][0]["project"] == "alpha"
    assert "OAuth" in results["items"][0]["snippet"]


def test_summary_reads_readme_headings(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    summary = summarize_project(root, project="alpha")
    assert summary["title"] == "Alpha"
    assert "Usage" in summary["headings"]


def test_security_blocks_traversal_and_non_markdown(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    with pytest.raises(ProjectDocsSecurityError):
        document_path(root, "alpha", "../beta/README.markdown")
    with pytest.raises(ProjectDocsSecurityError):
        document_path(root, "alpha", "secret.txt")


def test_security_blocks_symlink_escape(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    link = root / "alpha" / "outside.md"
    link.symlink_to(outside)
    with pytest.raises(ProjectDocsSecurityError):
        document_path(root, "alpha", "outside.md")


def test_server_dispatch_tools_call(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    server = ProjectDocsMcpServer(root)
    init = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init and init["result"]["capabilities"]["tools"]["listChanged"] is False
    tools = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools and len(tools["result"]["tools"]) == 5
    call = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "project_docs_search", "arguments": {"query": "CoreMCP"}},
        }
    )
    assert call and call["result"]["isError"] is False
    assert call["result"]["structuredContent"]["count"] == 1


def test_resolve_root_requires_directory(tmp_path: Path) -> None:
    with pytest.raises(ProjectDocsSecurityError):
        resolve_root(str(tmp_path / "missing"))
