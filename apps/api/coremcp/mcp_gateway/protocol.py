SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-11-25")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LATEST_PROTOCOL_VERSION = "2025-11-25"


def negotiate_protocol_version(requested: str | None) -> str:
    """Negotiate MCP protocol version.

    - Missing version keeps Claude Code compatibility by assuming 2025-06-18.
    - Supported versions are echoed.
    - Future versions are downgraded to the latest supported version.
    - Other unsupported versions currently fall back to the latest supported
      version for P0 compatibility.
    """

    if not requested:
        return DEFAULT_PROTOCOL_VERSION
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    if requested > LATEST_PROTOCOL_VERSION:
        return LATEST_PROTOCOL_VERSION
    return LATEST_PROTOCOL_VERSION
