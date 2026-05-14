from __future__ import annotations

from demo_mcp_suite.runtime import DemoMcpServer
from demo_mcp_suite.servers.bookmark_research import SERVER as BOOKMARK_RESEARCH_SERVER
from demo_mcp_suite.servers.design_assets import SERVER as DESIGN_ASSETS_SERVER
from demo_mcp_suite.servers.finance_ledger import SERVER as FINANCE_LEDGER_SERVER
from demo_mcp_suite.servers.home_lab import SERVER as HOME_LAB_SERVER
from demo_mcp_suite.servers.knowledge_vault import SERVER as KNOWLEDGE_VAULT_SERVER
from demo_mcp_suite.servers.personal_ops import SERVER as PERSONAL_OPS_SERVER
from demo_mcp_suite.servers.task_board import SERVER as TASK_BOARD_SERVER
from demo_mcp_suite.servers.travel_planner import SERVER as TRAVEL_PLANNER_SERVER

SERVERS: list[DemoMcpServer] = [
    PERSONAL_OPS_SERVER,
    KNOWLEDGE_VAULT_SERVER,
    TASK_BOARD_SERVER,
    BOOKMARK_RESEARCH_SERVER,
    DESIGN_ASSETS_SERVER,
    FINANCE_LEDGER_SERVER,
    HOME_LAB_SERVER,
    TRAVEL_PLANNER_SERVER,
]
