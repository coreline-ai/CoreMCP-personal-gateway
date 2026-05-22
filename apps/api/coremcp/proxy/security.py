from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from coremcp.errors import CoreMcpValueError
from coremcp.settings import Settings


class UrlSafetyError(CoreMcpValueError):
    pass


@dataclass(slots=True)
class UrlSafetyResult:
    url: str
    host: str
    resolved_ips: list[str]
    allowed_by: str
    scheme: str = ""
    port: int | None = None
    path: str = ""
    normalized_url: str = ""
    destination_fingerprint: str = ""


class UrlSafetyChecker:
    """SSRF guard for user-registered downstream MCP endpoints."""

    METADATA_IP = ipaddress.ip_address("169.254.169.254")
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
        _, scheme, host, port, path, normalized_url, fingerprint = self._parse_destination(url)
        if self._is_metadata_host(host):
            raise UrlSafetyError("AWS/GCP metadata endpoint is blocked")
        allowed_by = "host_allowlist" if host in self.allow_hosts else "public_dns"

        try:
            ips = self._resolve(host)
        except UrlSafetyError:
            if allowed_by != "host_allowlist":
                raise
            # Keep explicit host allowlist entries usable for dev/mock names
            # that are only resolvable in the eventual transport environment.
            # Resolvable allowlisted hosts are still resolved and rechecked below.
            ips = []
        if not ips:
            if allowed_by != "host_allowlist":
                raise UrlSafetyError("Endpoint hostname could not be resolved")

        for ip in ips:
            if self._is_metadata_ip(ip):
                raise UrlSafetyError("AWS/GCP metadata endpoint is blocked")
            if allowed_by == "host_allowlist":
                continue
            if self._ip_allowed_by_cidr(ip):
                continue
            if self._is_private_like(ip):
                raise UrlSafetyError(f"Private or unsafe address is blocked: {ip}")

        resolved_ips = [str(ip) for ip in ips]
        if host in self.allow_hosts:
            return UrlSafetyResult(
                url=url,
                host=host,
                resolved_ips=resolved_ips,
                allowed_by="host_allowlist",
                scheme=scheme,
                port=port,
                path=path,
                normalized_url=normalized_url,
                destination_fingerprint=fingerprint,
            )
        return UrlSafetyResult(
            url=url,
            host=host,
            resolved_ips=resolved_ips,
            allowed_by=allowed_by,
            scheme=scheme,
            port=port,
            path=path,
            normalized_url=normalized_url,
            destination_fingerprint=fingerprint,
        )

    def assert_same_safe_destination(self, before: UrlSafetyResult, url: str) -> UrlSafetyResult:
        """Re-resolve before an outbound request to reduce DNS rebinding risk."""

        _, scheme, host, port, path, _, fingerprint = self._parse_destination(url)
        if before.scheme and before.scheme != scheme:
            raise UrlSafetyError("Endpoint scheme changed before downstream request")
        if before.host != host:
            raise UrlSafetyError("Endpoint host changed before downstream request")
        if before.port is not None and before.port != port:
            raise UrlSafetyError("Endpoint port changed before downstream request")
        if before.path and before.path != path:
            raise UrlSafetyError("Endpoint path changed before downstream request")
        if before.destination_fingerprint and before.destination_fingerprint != fingerprint:
            raise UrlSafetyError("Endpoint destination changed before downstream request")

        after = self.assert_safe(url)
        if set(before.resolved_ips) != set(after.resolved_ips):
            raise UrlSafetyError("Endpoint DNS changed before downstream request")
        return after

    def _resolve(self, host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        try:
            records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UrlSafetyError("Endpoint hostname could not be resolved") from exc
        ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for record in records:
            address = record[4][0]
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if ip not in ips:
                ips.append(ip)
        return ips

    def _ip_allowed_by_cidr(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(ip in network for network in self.allow_networks)

    @classmethod
    def _is_metadata_host(cls, host: str) -> bool:
        try:
            return cls._is_metadata_ip(ipaddress.ip_address(host))
        except ValueError:
            return False

    @classmethod
    def _is_metadata_ip(cls, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if ip == cls.METADATA_IP:
            return True
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped == cls.METADATA_IP:
            return True
        return False

    def _parse_destination(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise UrlSafetyError("Only http/https MCP endpoints are allowed")
        if not parsed.hostname:
            raise UrlSafetyError("Endpoint URL must include a hostname")
        if parsed.username or parsed.password:
            raise UrlSafetyError("Credentials in endpoint URLs are not allowed")
        if parsed.fragment:
            raise UrlSafetyError("URL fragments are not allowed")
        try:
            port = parsed.port or self._default_port(parsed.scheme)
        except ValueError as exc:
            raise UrlSafetyError("Endpoint URL port is invalid") from exc
        host = parsed.hostname.lower()
        path = parsed.path or "/"
        normalized_url = self._normalized_url(parsed.scheme, host, port, path)
        fingerprint = self._destination_fingerprint(parsed.scheme, host, port, path)
        return parsed, parsed.scheme, host, port, path, normalized_url, fingerprint

    @staticmethod
    def _default_port(scheme: str) -> int:
        return 443 if scheme == "https" else 80

    @staticmethod
    def _normalized_url(scheme: str, host: str, port: int, path: str) -> str:
        return f"{scheme}://{host}:{port}{path}"

    @staticmethod
    def _destination_fingerprint(scheme: str, host: str, port: int, path: str) -> str:
        return f"{scheme}|{host}|{port}|{path}"

    @classmethod
    def _is_private_like(cls, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return bool(
            any(ip in network for network in cls.UNSAFE_NETWORKS)
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
