"""Native MCP tools on the chat path: build_mcp_toolkits selection logic.

The chat route attaches these (connected) toolkits to the agno Agent via
get_agent(extra_tools=...); see agents/agent.py and api/routes/v2/agents.py.
"""

from agents.agent import build_mcp_toolkits


class _S:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _cfg(servers):
    return _S(worker_config=_S(mcp_servers=servers))


def test_none_config_returns_empty():
    assert build_mcp_toolkits(None) == []


def test_no_worker_config_returns_empty():
    assert build_mcp_toolkits(_S()) == []


def test_no_mcp_servers_returns_empty():
    assert build_mcp_toolkits(_S(worker_config=_S(mcp_servers=None))) == []


def test_http_server_builds_one_toolkit():
    cfg = _cfg([_S(name="pf", type="http", url="https://x/mcp/", headers={"Authorization": "Bearer z"})])
    toolkits = build_mcp_toolkits(cfg)
    assert len(toolkits) == 1
    # agno MCPTools is an async context manager the route connects before the run.
    assert hasattr(toolkits[0], "__aenter__") and hasattr(toolkits[0], "__aexit__")


def test_streamable_http_type_accepted():
    cfg = _cfg([_S(type="streamable-http", url="https://x/mcp/", headers=None)])
    assert len(build_mcp_toolkits(cfg)) == 1


def test_unsupported_transports_are_ignored_with_warnings(caplog):
    cfg = _cfg(
        [
            _S(name="git", type="stdio", url=None),
            _S(name="legacy", type="sse", url="https://x"),
            _S(name="default", url=None),
        ]
    )

    assert build_mcp_toolkits(cfg) == []

    assert "git" in caplog.text
    assert "stdio" in caplog.text
    assert "legacy" in caplog.text
    assert "sse" in caplog.text
    assert "default" in caplog.text


def test_http_server_without_url_is_ignored_with_warning(caplog):
    cfg = _cfg([_S(name="missing-url", type="http", url=None)])

    assert build_mcp_toolkits(cfg) == []

    assert "missing-url" in caplog.text
    assert "http" in caplog.text
    assert "missing URL" in caplog.text
