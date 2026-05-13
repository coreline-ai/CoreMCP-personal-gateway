from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from coremcp.settings import Settings


class UrlSafetyError(ValueError):
    pass


@dataclass(slots=True)
class UrlSafetyResult:
    url: str
    host: str
    resolved_ips: list[str]
    allowed_by: str


class UrlSafetyChecker:
    """SSRF guard for user-registered downstream MCP endpoints."""

    UNSAFE_NETWORKS = tuple(
        ipaddress.ip_network(cidr)
        for cidr in (
            "0.0.0.0/8",
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "224.0.0.0/4",
            "::/128",
            "::1/128",
            "fc00::/7",
            "fe80::/10",
            "ff00::/8",
        )
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.allow_hosts = settings.ssrf_allow_host_set
        self.allow_networks = [ipaddress.ip_network(cidr, strict=False) for cidr in settings.ssrf_allow_cidr_list]
        if settings.allow_tailscale_downstream:
            self.allow_networks.append(ipaddress.ip_network("100.64.0.0/10"))

    def assert_safe(self, url: str) -> UrlSafetyResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise UrlSafetyError("Only http/https MCP endpoints are allowed")
        if not parsed.hostname:
            raise UrlSafetyError("Endpoint URL must include a hostname")
        if parsed.username or parsed.password:
            raise UrlSafetyError("Credentials in endpoint URLs are not allowed")
        if parsed.fragment:
            raise UrlSafetyError("URL fragments are not allowed")

        host = parsed.hostname.lower()
        if host == "169.254.169.254":
            raise UrlSafetyError("AWS/GCP metadata endpoint is blocked")
        if host in self.allow_hosts:
            return UrlSafetyResult(url=url, host=host, resolved_ips=[], allowed_by="host_allowlist")

        ips = self._resolve(host)
        if not ips:
            raise UrlSafetyError("Endpoint hostname could not be resolved")
        for ip in ips:
            if str(ip) == "169.254.169.254":
                raise UrlSafetyError("AWS/GCP metadata endpoint is blocked")
            if self._ip_allowed_by_cidr(ip):
                continue
            if self._is_private_like(ip):
                raise UrlSafetyError(f"Private or unsafe address is blocked: {ip}")
        return UrlSafetyResult(url=url, host=host, resolved_ips=[str(ip) for ip in ips], allowed_by="public_dns")

    def assert_same_safe_destination(self, before: UrlSafetyResult, url: str) -> UrlSafetyResult:
        """Re-resolve before an outbound request to reduce DNS rebinding risk."""

        after = self.assert_safe(url)
        if before.allowed_by == "host_allowlist" or after.allowed_by == "host_allowlist":
            return after
        if before.host != after.host:
            raise UrlSafetyError("Endpoint host changed before downstream request")
        if set(before.resolved_ips) != set(after.resolved_ips):
            raise UrlSafetyError("Endpoint DNS changed before downstream request")
        return after

    def _resolve(self, host: str) -> list[ipaddress._BaseAddress]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        try:
            records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UrlSafetyError("Endpoint hostname could not be resolved") from exc
        ips: list[ipaddress._BaseAddress] = []
        for record in records:
            address = record[4][0]
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if ip not in ips:
                ips.append(ip)
        return ips

    def _ip_allowed_by_cidr(self, ip: ipaddress._BaseAddress) -> bool:
        return any(ip in network for network in self.allow_networks)

    @classmethod
    def _is_private_like(cls, ip: ipaddress._BaseAddress) -> bool:
        return bool(
            any(ip in network for network in cls.UNSAFE_NETWORKS)
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
