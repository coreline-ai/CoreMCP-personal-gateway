from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import httpx
import pytest

from coremcp.proxy import DownstreamMcpClient, UrlSafetyChecker, UrlSafetyError
from coremcp.settings import Settings


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token", **kwargs)


def test_assert_same_safe_destination_blocks_dns_relookup_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checker = UrlSafetyChecker(_settings(tmp_path))
    resolutions = iter(
        [
            [ipaddress.ip_address("93.184.216.34")],
            [ipaddress.ip_address("93.184.216.35")],
        ]
    )

    monkeypatch.setattr(checker, "_resolve", lambda host: next(resolutions))

    before = checker.assert_safe("https://example.com/mcp")
    with pytest.raises(UrlSafetyError, match="DNS changed"):
        checker.assert_same_safe_destination(before, "https://example.com/mcp")


def test_assert_same_safe_destination_blocks_destination_shape_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checker = UrlSafetyChecker(_settings(tmp_path))
    monkeypatch.setattr(checker, "_resolve", lambda host: [ipaddress.ip_address("93.184.216.34")])

    before = checker.assert_safe("https://example.com/mcp")

    for changed_url in (
        "http://example.com/mcp",
        "https://www.example.com/mcp",
        "https://example.com:8443/mcp",
        "https://example.com/other",
    ):
        with pytest.raises(UrlSafetyError, match="Endpoint .* changed|Endpoint destination changed"):
            checker.assert_same_safe_destination(before, changed_url)


def test_host_allowlist_still_allows_host_but_not_scheme_host_port_or_path_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    checker = UrlSafetyChecker(_settings(tmp_path, COREMCP_SSRF_ALLOW_HOSTS="internal.example"))
    monkeypatch.setattr(checker, "_resolve", lambda host: [ipaddress.ip_address("10.0.0.10")])
    before = checker.assert_safe("https://internal.example/mcp")

    assert checker.assert_same_safe_destination(before, "https://internal.example/mcp").allowed_by == "host_allowlist"

    for changed_url in (
        "http://internal.example/mcp",
        "https://other.example/mcp",
        "https://internal.example:8443/mcp",
        "https://internal.example/other",
    ):
        with pytest.raises(UrlSafetyError, match="Endpoint .* changed|Endpoint destination changed"):
            checker.assert_same_safe_destination(before, changed_url)


def test_host_allowlist_resolves_private_ip_and_populates_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    checker = UrlSafetyChecker(_settings(tmp_path, COREMCP_SSRF_ALLOW_HOSTS="internal.example"))
    monkeypatch.setattr(checker, "_resolve", lambda host: [ipaddress.ip_address("10.0.0.10")])

    result = checker.assert_safe("https://internal.example/mcp")

    assert result.allowed_by == "host_allowlist"
    assert result.resolved_ips == ["10.0.0.10"]


def test_host_allowlist_still_blocks_metadata_ip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checker = UrlSafetyChecker(_settings(tmp_path, COREMCP_SSRF_ALLOW_HOSTS="internal.example"))
    monkeypatch.setattr(checker, "_resolve", lambda host: [ipaddress.ip_address("169.254.169.254")])

    with pytest.raises(UrlSafetyError, match="metadata"):
        checker.assert_safe("https://internal.example/mcp")


def test_host_allowlist_blocks_dns_change_before_downstream_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    checker = UrlSafetyChecker(_settings(tmp_path, COREMCP_SSRF_ALLOW_HOSTS="internal.example"))
    resolutions = iter(
        [
            [ipaddress.ip_address("10.0.0.10"), ipaddress.ip_address("10.0.0.11")],
            [ipaddress.ip_address("10.0.0.11"), ipaddress.ip_address("10.0.0.10")],
            [ipaddress.ip_address("10.0.0.12")],
        ]
    )
    monkeypatch.setattr(checker, "_resolve", lambda host: next(resolutions))

    before = checker.assert_safe("https://internal.example/mcp")
    assert checker.assert_same_safe_destination(before, "https://internal.example/mcp").resolved_ips == [
        "10.0.0.11",
        "10.0.0.10",
    ]

    with pytest.raises(UrlSafetyError, match="DNS changed"):
        checker.assert_same_safe_destination(before, "https://internal.example/mcp")


def test_allow_tailscale_downstream_preserves_cgnat_allowance(tmp_path: Path):
    checker = UrlSafetyChecker(_settings(tmp_path))
    with pytest.raises(UrlSafetyError):
        checker.assert_safe("http://100.64.0.1/mcp")

    tailscale_checker = UrlSafetyChecker(_settings(tmp_path, ALLOW_TAILSCALE_DOWNSTREAM=True))
    result = tailscale_checker.assert_safe("http://100.64.0.1/mcp")

    assert result.allowed_by == "public_dns"
    assert result.normalized_url == "http://100.64.0.1:80/mcp"


@pytest.mark.asyncio
async def test_downstream_uses_resolved_ip_pinning_for_dns_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checker = UrlSafetyChecker(_settings(tmp_path))
    monkeypatch.setattr(checker, "_resolve", lambda host: [ipaddress.ip_address("93.184.216.34")])
    observed: dict[str, object] = {}

    async def transport(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["host"] = request.headers.get("host")
        observed["sni_hostname"] = request.extensions.get("sni_hostname")
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    downstream = DownstreamMcpClient("https://example.com/mcp", client)

    try:
        await downstream.request(method="tools/list", url_safety_checker=checker)
    finally:
        await client.aclose()

    assert observed["url"] == "https://93.184.216.34/mcp"
    assert observed["host"] == "example.com"
    assert observed["sni_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_downstream_pins_resolved_host_allowlist_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checker = UrlSafetyChecker(_settings(tmp_path, COREMCP_SSRF_ALLOW_HOSTS="fake.local"))
    monkeypatch.setattr(checker, "_resolve", lambda host: [ipaddress.ip_address("10.0.0.10")])
    observed: dict[str, object] = {}

    async def transport(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["host"] = request.headers.get("host")
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    downstream = DownstreamMcpClient("http://fake.local/mcp", client)

    try:
        await downstream.request(method="tools/list", url_safety_checker=checker)
    finally:
        await client.aclose()

    assert observed["url"] == "http://10.0.0.10/mcp"
    assert observed["host"] == "fake.local"
