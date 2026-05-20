# Project Docs MCP

Read-only MCP server for `/Users/hwanchoi/projects` style project folders.

It exposes only Markdown documents (`.md`, `.markdown`) and blocks path traversal,
symlink escape, write/delete operations, and large response payloads.

## Tools

- `project_list` — list projects under the configured root.
- `project_docs_list` — list Markdown files for one project.
- `project_docs_search` — search Markdown documents by keyword.
- `project_doc_read` — read a Markdown document with truncation.
- `project_summary` — summarize one project from README headings/snippet.

## Runtime

```bash
PROJECT_DOCS_ROOT=/Users/hwanchoi/projects python3 -m project_docs_mcp
```

## CoreMCP registration

From the CoreMCP repository root:

```bash
make project-docs-register
make codex-smoke
infra/scripts/codex-exec-coremcp.sh "project_docs.project_list 도구로 전체 프로젝트 목록을 보고 카테고리별로 정리해줘"
```

The registration script creates or updates a `project_docs` stdio service and adds
it to the default toolbox. Exposed tools are namespaced as:

- `project_docs.project_list`
- `project_docs.project_docs_list`
- `project_docs.project_docs_search`
- `project_docs.project_doc_read`
- `project_docs.project_summary`
