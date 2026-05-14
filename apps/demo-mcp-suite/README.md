# CoreMCP Demo MCP Suite

Eight local demo MCP servers for CoreMCP personal gateway demos.

## Run

```bash
cd apps/demo-mcp-suite
uv run demo-mcp-suite
```

Default base URL:

```text
http://127.0.0.1:8791
```

## Endpoints

| Demo | MCP endpoint | Suggested CoreMCP service slug |
|---|---|---|
| Personal Ops Desk MCP | `/personal-ops/mcp` | `demo_ops` |
| Local Knowledge Vault MCP | `/knowledge-vault/mcp` | `demo_knowledge` |
| Project Task Board MCP | `/task-board/mcp` | `demo_tasks` |
| Bookmark Research MCP | `/bookmark-research/mcp` | `demo_bookmarks` |
| Design Asset Catalog MCP | `/design-assets/mcp` | `demo_design` |
| Fake Finance Ledger MCP | `/finance-ledger/mcp` | `demo_finance` |
| Home Lab Status MCP | `/home-lab/mcp` | `demo_home_lab` |
| Travel Planner MCP | `/travel-planner/mcp` | `demo_travel` |

## CoreMCP registration payloads

```bash
curl http://127.0.0.1:8791/demo-services
```

Each item can be used as a `POST /v1/mcp-services` body.

Example:

```bash
curl -X POST http://127.0.0.1:8787/v1/mcp-services \
  -H "Authorization: Bearer $COREMCP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Personal Ops Desk MCP",
    "slug": "demo_ops",
    "description": "가상의 개인 운영 데스크 MCP",
    "endpoint_url": "http://127.0.0.1:8791/personal-ops/mcp",
    "auth_type": "none",
    "category": "demo"
  }'
```

## Tool Matrix

| Demo | Read-only tools | Write tools | Destructive tools |
|---|---|---|---|
| Personal Ops Desk | `ops_status`, `ops_checklist`, `incident_list` | `note_create`, `backup_run` | `service_restart` |
| Local Knowledge Vault | `note_search`, `note_get` | `note_create`, `note_tag` | `note_delete` |
| Project Task Board | `task_list`, `task_get` | `task_create`, `task_update_status` | `task_archive` |
| Bookmark Research | `bookmark_search`, `bookmark_list_by_tag`, `bookmark_summarize_stub` | `bookmark_create` | `bookmark_delete` |
| Design Asset Catalog | `asset_search`, `color_tokens`, `component_get` | `asset_register` | `asset_deprecate` |
| Fake Finance Ledger | `ledger_summary`, `transaction_search` | `transaction_create`, `transaction_categorize` | `transaction_delete` |
| Home Lab Status | `device_list`, `device_status`, `service_logs` | `maintenance_note_create` | `service_restart` |
| Travel Planner | `itinerary_list`, `place_search` | `itinerary_add_place` | `itinerary_remove_place` |

## CoreMCP demo flow

1. Run the suite with `make demo-run`.
2. Open Web Admin → Services.
3. Register one or more `/demo-services` payloads.
4. Validate each service.
5. Add to default 도구함.
6. In Service Detail → Tools, try presets:
   - `readonly`
   - `dangerous_off`
   - `full_access`
7. Call tools from Playground.

## Tests

```bash
cd apps/demo-mcp-suite
uv run pytest -q
```
