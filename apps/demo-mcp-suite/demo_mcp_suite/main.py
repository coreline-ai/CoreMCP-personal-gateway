from __future__ import annotations

import uvicorn

from demo_mcp_suite.registry import SERVERS
from demo_mcp_suite.runtime import create_demo_app

app = create_demo_app(SERVERS)


def run() -> None:
    uvicorn.run("demo_mcp_suite.main:app", host="127.0.0.1", port=8791, reload=False)


if __name__ == "__main__":
    run()
