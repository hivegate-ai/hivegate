"""What an MCP server definition must look like for chat agents to connect to it.

Shared by the three places that check it: the MCP server registry (/v2/mcp-servers), the
chat request's check of servers embedded in an agent config, and build_mcp_toolkits. They
used to carry their own copies of the transport list, and drifted: the chat check lowercased
the transport and build_mcp_toolkits did not, so "HTTP" passed validation and was then
silently skipped.
"""

from typing import Any, Optional
from urllib.parse import urlsplit

SUPPORTED_MCP_TRANSPORTS = ("http", "streamable-http")
VALID_HTTP_SCHEMES = ("http", "https")


def normalize_mcp_transport(value: Any) -> Optional[str]:
    """The transport in canonical form, or None when chat agents cannot use it."""
    if value is None:
        return None
    transport = str(value).strip().lower()
    return transport if transport in SUPPORTED_MCP_TRANSPORTS else None


def mcp_url_problem(url: Any, *, allow_userinfo: bool = True) -> Optional[str]:
    """Why this URL cannot be connected to, or None when it can.

    Checks the scheme, the host and the port (urlsplit only parses the port when .port is
    read, so http://host:99999/ would otherwise pass and fail only at connect time).
    """
    text = str(url).strip() if url is not None else ""
    if not text:
        return "missing"
    try:
        parsed = urlsplit(text)
        parsed.port  # raises ValueError on a non-numeric or out-of-range port
        has_userinfo = parsed.username is not None or parsed.password is not None
    except ValueError:
        return "invalid"
    if parsed.scheme.lower() not in VALID_HTTP_SCHEMES or not parsed.hostname:
        return "invalid"
    if has_userinfo and not allow_userinfo:
        return "userinfo"
    return None
