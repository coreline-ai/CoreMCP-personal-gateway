#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def read_json(url: str, token: str | None = None, timeout: float = 5.0) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local/operator-provided URL smoke.
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a lightweight CoreMCP long-soak readiness loop.")
    parser.add_argument("--api-url", default=os.getenv("COREMCP_API_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--admin-token-file", default=os.getenv("COREMCP_ADMIN_TOKEN_FILE", os.path.expanduser("~/.coremcp/admin-token")))
    parser.add_argument("--duration-seconds", type=int, default=int(os.getenv("COREMCP_SOAK_DURATION_SECONDS", "300")))
    parser.add_argument("--interval-seconds", type=int, default=int(os.getenv("COREMCP_SOAK_INTERVAL_SECONDS", "30")))
    parser.add_argument("--max-failures", type=int, default=int(os.getenv("COREMCP_SOAK_MAX_FAILURES", "0")))
    args = parser.parse_args()

    token = None
    try:
        with open(os.path.expanduser(args.admin_token_file), "r", encoding="utf-8") as handle:
            token = handle.read().strip() or None
    except FileNotFoundError:
        token = None

    deadline = time.time() + max(1, args.duration_seconds)
    failures = 0
    checks = 0
    events: list[dict] = []
    while time.time() < deadline:
        checks += 1
        started = time.time()
        event = {"check": checks, "ts": round(started, 3)}
        try:
            ready = read_json(f"{args.api_url.rstrip('/')}/ready")
            event["ready"] = ready.get("status")
            if ready.get("status") != "ready":
                raise RuntimeError(f"ready status is {ready.get('status')}")
            if token:
                dashboard = read_json(f"{args.api_url.rstrip('/')}/v1/dashboard/summary", token=token)
                event["services"] = dashboard.get("metrics", {}).get("mcp_services_total")
                event["health_failing"] = dashboard.get("metrics", {}).get("mcp_services_health_failing")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            failures += 1
            event["error"] = str(exc)
        event["latency_ms"] = round((time.time() - started) * 1000)
        events.append(event)
        print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
        if failures > args.max_failures:
            print(json.dumps({"status": "failed", "checks": checks, "failures": failures}, sort_keys=True), file=sys.stderr)
            return 1
        time.sleep(max(1, args.interval_seconds))

    print(json.dumps({"status": "passed", "checks": checks, "failures": failures}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
