from __future__ import annotations

import copy
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_INITIAL_DEVICES: dict[str, dict[str, Any]] = {
    "nas-01": {
        "id": "nas-01",
        "name": "Vault NAS",
        "kind": "storage",
        "location": "rack-a / bay-2",
        "status": "online",
        "summary": "Nightly backups and shared folders are healthy.",
        "metrics": {
            "cpu_percent": 18,
            "memory_percent": 62,
            "disk_percent": 71,
            "temperature_c": 38,
            "uptime_hours": 740,
        },
        "service_ids": ["smb", "backup-agent", "photo-indexer"],
        "last_checked": "2026-05-14T09:00:00+09:00",
    },
    "router-01": {
        "id": "router-01",
        "name": "Edge Router",
        "kind": "network",
        "location": "rack-a / bay-1",
        "status": "warning",
        "summary": "Primary WAN is healthy; LTE failover SIM needs renewal within 7 days.",
        "metrics": {
            "cpu_percent": 24,
            "memory_percent": 48,
            "wan_latency_ms": 21,
            "vpn_peers": 3,
            "uptime_hours": 1285,
        },
        "service_ids": ["dns-filter", "wireguard"],
        "last_checked": "2026-05-14T09:00:00+09:00",
    },
    "pi-01": {
        "id": "pi-01",
        "name": "Automation Pi",
        "kind": "automation",
        "location": "office shelf",
        "status": "online",
        "summary": "Home automation automations and MQTT bridge are running.",
        "metrics": {
            "cpu_percent": 11,
            "memory_percent": 54,
            "sd_card_percent": 43,
            "temperature_c": 41,
            "uptime_hours": 312,
        },
        "service_ids": ["home-assistant", "mqtt-bridge"],
        "last_checked": "2026-05-14T09:00:00+09:00",
    },
}

_INITIAL_SERVICES: dict[str, dict[str, Any]] = {
    "smb": {
        "id": "smb",
        "name": "SMB File Share",
        "device_id": "nas-01",
        "state": "running",
        "port": 445,
        "restart_count": 0,
        "last_restart": "2026-05-10T02:15:00+09:00",
        "impact": "Shared folders may be unavailable for about 20 seconds during restart.",
    },
    "backup-agent": {
        "id": "backup-agent",
        "name": "Backup Agent",
        "device_id": "nas-01",
        "state": "running",
        "port": None,
        "restart_count": 0,
        "last_restart": "2026-05-11T03:05:00+09:00",
        "impact": "In-flight backup jobs would be rescheduled in this demo model.",
    },
    "photo-indexer": {
        "id": "photo-indexer",
        "name": "Photo Indexer",
        "device_id": "nas-01",
        "state": "running",
        "port": 2283,
        "restart_count": 0,
        "last_restart": "2026-05-12T01:10:00+09:00",
        "impact": "Photo search and thumbnail generation pause briefly.",
    },
    "dns-filter": {
        "id": "dns-filter",
        "name": "DNS Filter",
        "device_id": "router-01",
        "state": "running",
        "port": 53,
        "restart_count": 0,
        "last_restart": "2026-05-09T04:30:00+09:00",
        "impact": "DNS answers may fall back to router cache for a few seconds.",
    },
    "wireguard": {
        "id": "wireguard",
        "name": "WireGuard VPN",
        "device_id": "router-01",
        "state": "running",
        "port": 51820,
        "restart_count": 0,
        "last_restart": "2026-05-08T05:00:00+09:00",
        "impact": "Remote VPN peers reconnect automatically.",
    },
    "home-assistant": {
        "id": "home-assistant",
        "name": "Home Assistant",
        "device_id": "pi-01",
        "state": "running",
        "port": 8123,
        "restart_count": 0,
        "last_restart": "2026-05-13T06:45:00+09:00",
        "impact": "Automations pause while the demo restart event is recorded.",
    },
    "mqtt-bridge": {
        "id": "mqtt-bridge",
        "name": "MQTT Bridge",
        "device_id": "pi-01",
        "state": "running",
        "port": 1883,
        "restart_count": 0,
        "last_restart": "2026-05-12T23:20:00+09:00",
        "impact": "Sensor telemetry buffers locally during restart.",
    },
}

_INITIAL_LOGS: dict[str, list[dict[str, Any]]] = {
    "smb": [
        {
            "sequence": 1001,
            "timestamp": "2026-05-14T08:40:00+09:00",
            "level": "info",
            "message": "Share audit completed: 8 active sessions, no permission drift.",
        },
        {
            "sequence": 1002,
            "timestamp": "2026-05-14T08:55:00+09:00",
            "level": "info",
            "message": "Time Machine sparsebundle health check passed.",
        },
    ],
    "backup-agent": [
        {
            "sequence": 1010,
            "timestamp": "2026-05-14T03:20:00+09:00",
            "level": "info",
            "message": "Nightly snapshot finished: 1.8 TiB protected, 0 failed paths.",
        },
        {
            "sequence": 1011,
            "timestamp": "2026-05-14T03:45:00+09:00",
            "level": "info",
            "message": "Offsite replication queued for weekend window.",
        },
    ],
    "photo-indexer": [
        {
            "sequence": 1020,
            "timestamp": "2026-05-14T07:15:00+09:00",
            "level": "info",
            "message": "Indexed 42 new photos from mobile upload folder.",
        }
    ],
    "dns-filter": [
        {
            "sequence": 1030,
            "timestamp": "2026-05-14T08:50:00+09:00",
            "level": "warning",
            "message": "LTE failover subscription expires in 7 days.",
        }
    ],
    "wireguard": [
        {
            "sequence": 1040,
            "timestamp": "2026-05-14T08:45:00+09:00",
            "level": "info",
            "message": "Peer laptop-mbp handshake completed from trusted address.",
        }
    ],
    "home-assistant": [
        {
            "sequence": 1050,
            "timestamp": "2026-05-14T08:30:00+09:00",
            "level": "info",
            "message": "Automation morning-light completed in 420 ms.",
        },
        {
            "sequence": 1051,
            "timestamp": "2026-05-14T08:35:00+09:00",
            "level": "info",
            "message": "Zigbee network health: 29 online devices, 0 offline devices.",
        },
    ],
    "mqtt-bridge": [
        {
            "sequence": 1060,
            "timestamp": "2026-05-14T08:58:00+09:00",
            "level": "info",
            "message": "Published 124 retained sensor messages after broker reconnect.",
        }
    ],
}

_INITIAL_NOTES: list[dict[str, Any]] = [
    {
        "id": "note-001",
        "device_id": "router-01",
        "severity": "warning",
        "message": "Renew LTE failover SIM before the next travel week.",
        "created_at": "2026-05-14T08:00:00+09:00",
    }
]

_devices: dict[str, dict[str, Any]] = {}
_services: dict[str, dict[str, Any]] = {}
_logs: dict[str, list[dict[str, Any]]] = {}
_notes: list[dict[str, Any]] = []
_note_sequence = 1
_log_sequence = 1000
_restart_sequence = 0


def _timestamp(sequence: int) -> str:
    minute = sequence % 60
    hour = 9 + ((sequence // 60) % 8)
    return f"2026-05-14T{hour:02d}:{minute:02d}:00+09:00"


def _reset_state() -> None:
    global _devices, _services, _logs, _notes, _note_sequence, _log_sequence, _restart_sequence
    _devices = copy.deepcopy(_INITIAL_DEVICES)
    _services = copy.deepcopy(_INITIAL_SERVICES)
    _logs = copy.deepcopy(_INITIAL_LOGS)
    _notes = copy.deepcopy(_INITIAL_NOTES)
    _note_sequence = len(_notes) + 1
    _log_sequence = max(entry["sequence"] for entries in _logs.values() for entry in entries) + 1
    _restart_sequence = 0


def _error_result(message: str, **structured: Any) -> dict[str, Any]:
    payload = {"error": message, **structured}
    result = text_result(message, payload)
    result["isError"] = True
    return result


def _optional_bool(arguments: dict[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if isinstance(value, bool):
        return value
    return default


def _optional_limit(arguments: dict[str, Any], *, default: int, minimum: int = 1, maximum: int = 50) -> int:
    value = arguments.get("limit", default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(value, minimum), maximum)
    return default


def _required_text(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _public_service(service: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(service)
    device = _devices.get(service["device_id"])
    item["device_name"] = device["name"] if device else "unknown device"
    return item


def _public_device(device: dict[str, Any], *, include_services: bool) -> dict[str, Any]:
    item = copy.deepcopy(device)
    service_ids = item.pop("service_ids")
    item["service_count"] = len(service_ids)
    item["open_alert_count"] = sum(1 for note in _notes if note["device_id"] == item["id"])
    if include_services:
        item["services"] = [_public_service(_services[service_id]) for service_id in service_ids]
    return item


def _device_list(arguments: dict[str, Any]) -> dict[str, Any]:
    include_services = _optional_bool(arguments, "include_services", True)
    devices = [_public_device(device, include_services=include_services) for device in _devices.values()]
    status_counts = {
        status: sum(1 for device in devices if device["status"] == status)
        for status in sorted({device["status"] for device in devices})
    }
    summary = ", ".join(f"{count} {status}" for status, count in status_counts.items())
    return text_result(
        f"{len(devices)} home lab devices monitored ({summary}).",
        {
            "devices": devices,
            "summary": {
                "total": len(devices),
                "status_counts": status_counts,
                "note_count": len(_notes),
            },
        },
    )


def _device_status(arguments: dict[str, Any]) -> dict[str, Any]:
    device_id = _required_text(arguments, "device_id")
    if device_id is None:
        return _error_result("device_id is required", required=["device_id"])
    device = _devices.get(device_id)
    if device is None:
        return _error_result("Unknown home lab device", device_id=device_id, known_device_ids=sorted(_devices))

    service_ids = device["service_ids"]
    services = [_public_service(_services[service_id]) for service_id in service_ids]
    notes = [copy.deepcopy(note) for note in _notes if note["device_id"] == device_id]
    return text_result(
        f"{device['name']} is {device['status']} with {len(services)} service(s) tracked.",
        {
            "device": _public_device(device, include_services=False),
            "services": services,
            "maintenance_notes": notes,
        },
    )


def _service_logs(arguments: dict[str, Any]) -> dict[str, Any]:
    service_id = _required_text(arguments, "service_id")
    if service_id is None:
        return _error_result("service_id is required", required=["service_id"])
    service = _services.get(service_id)
    if service is None:
        return _error_result("Unknown home lab service", service_id=service_id, known_service_ids=sorted(_services))

    limit = _optional_limit(arguments, default=10, maximum=25)
    entries = copy.deepcopy(_logs.get(service_id, [])[-limit:])
    return text_result(
        f"Returned {len(entries)} log entries for {service['name']}.",
        {
            "service": _public_service(service),
            "logs": entries,
            "limit": limit,
        },
    )


def _maintenance_note_create(arguments: dict[str, Any]) -> dict[str, Any]:
    global _note_sequence
    device_id = _required_text(arguments, "device_id")
    message = _required_text(arguments, "message")
    if device_id is None:
        return _error_result("device_id is required", required=["device_id", "message"])
    if message is None:
        return _error_result("message is required", required=["device_id", "message"])
    if device_id not in _devices:
        return _error_result("Unknown home lab device", device_id=device_id, known_device_ids=sorted(_devices))

    severity = arguments.get("severity", "info")
    if severity not in {"info", "warning", "critical"}:
        return _error_result(
            "severity must be one of info, warning, critical",
            severity=severity,
            allowed=["info", "warning", "critical"],
        )

    note = {
        "id": f"note-{_note_sequence:03d}",
        "device_id": device_id,
        "severity": severity,
        "message": message,
        "created_at": _timestamp(_note_sequence),
    }
    _note_sequence += 1
    _notes.append(note)
    return text_result(
        f"Created maintenance note {note['id']} for {_devices[device_id]['name']}.",
        {
            "note": copy.deepcopy(note),
            "device": _public_device(_devices[device_id], include_services=False),
            "note_count": len(_notes),
        },
    )


def _service_restart(arguments: dict[str, Any]) -> dict[str, Any]:
    global _log_sequence, _restart_sequence
    service_id = _required_text(arguments, "service_id")
    if service_id is None:
        return _error_result("service_id is required", required=["service_id"])
    service = _services.get(service_id)
    if service is None:
        return _error_result("Unknown home lab service", service_id=service_id, known_service_ids=sorted(_services))

    reason = _required_text(arguments, "reason") or "No reason supplied"
    _restart_sequence += 1
    service["state"] = "running"
    service["restart_count"] += 1
    service["last_restart"] = _timestamp(120 + _restart_sequence)

    log_entry = {
        "sequence": _log_sequence,
        "timestamp": service["last_restart"],
        "level": "warning",
        "message": f"Demo restart recorded by CoreMCP: {reason}",
    }
    _log_sequence += 1
    _logs.setdefault(service_id, []).append(log_entry)

    return text_result(
        f"Simulated restart for {service['name']}; no system command was executed.",
        {
            "service": _public_service(service),
            "log_entry": copy.deepcopy(log_entry),
            "safety": {
                "simulated_only": True,
                "external_commands_executed": False,
                "credential_accessed": False,
            },
        },
    )


_TOOLS = [
    tool(
        name="device_list",
        title="List home lab devices",
        description="Return the demo home lab device inventory with current fixture health summaries.",
        input_schema=object_schema(
            {
                "include_services": {
                    "type": "boolean",
                    "description": "Include tracked service details for each device.",
                    "default": True,
                }
            }
        ),
        read_only=True,
    ),
    tool(
        name="device_status",
        title="Get device status",
        description="Return health metrics, services, and maintenance notes for one home lab device.",
        input_schema=object_schema(
            {
                "device_id": {
                    "type": "string",
                    "description": "Fixture device id.",
                    "enum": sorted(_INITIAL_DEVICES),
                }
            },
            required=["device_id"],
        ),
        read_only=True,
    ),
    tool(
        name="service_logs",
        title="Read service logs",
        description="Return bounded in-memory log entries for a demo home lab service.",
        input_schema=object_schema(
            {
                "service_id": {
                    "type": "string",
                    "description": "Fixture service id.",
                    "enum": sorted(_INITIAL_SERVICES),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum entries to return; clamped to 1..25.",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 10,
                },
            },
            required=["service_id"],
        ),
        read_only=True,
    ),
    tool(
        name="maintenance_note_create",
        title="Create maintenance note",
        description="Add a local maintenance note to a fixture device; no external system is called.",
        input_schema=object_schema(
            {
                "device_id": {
                    "type": "string",
                    "description": "Fixture device id.",
                    "enum": sorted(_INITIAL_DEVICES),
                },
                "message": {
                    "type": "string",
                    "description": "Human-readable maintenance note.",
                    "minLength": 1,
                },
                "severity": {
                    "type": "string",
                    "description": "Operational severity for the note.",
                    "enum": ["info", "warning", "critical"],
                    "default": "info",
                },
            },
            required=["device_id", "message"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="service_restart",
        title="Restart service (simulated)",
        description="Record a destructive demo restart event for a service without running real commands.",
        input_schema=object_schema(
            {
                "service_id": {
                    "type": "string",
                    "description": "Fixture service id.",
                    "enum": sorted(_INITIAL_SERVICES),
                },
                "reason": {
                    "type": "string",
                    "description": "Why the connected AI client requested the restart.",
                },
            },
            required=["service_id"],
        ),
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
]

_HANDLERS = {
    "device_list": _device_list,
    "device_status": _device_status,
    "service_logs": _service_logs,
    "maintenance_note_create": _maintenance_note_create,
    "service_restart": _service_restart,
}

_reset_state()

SERVER = DemoMcpServer(
    slug="home-lab",
    service_slug="demo_home_lab",
    title="Home Lab Status MCP",
    description="가상의 홈랩 상태 MCP",
    tools=_TOOLS,
    handlers=_HANDLERS,
    reset=_reset_state,
)
