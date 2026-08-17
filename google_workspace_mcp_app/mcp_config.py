"""Builds this app's own root ``mcp.json`` — the file aw-mcp-gateway's app scan
reads directly (``scan_app_mcp_servers()``, rooted at ``AW_APP_SCAN_ROOTS``,
default ``/opt/aw-workspace/apps``).

``contributes.mcp.provides`` in aw-app.json registers **nothing**; it is the
marketplace's "what you get" list. The app has to write this file itself or it
installs clean, passes ``aw-workspace-cli doctor``, and serves zero tools.

Why ``http`` and not ``stdio``
------------------------------
The monolith ran this server as a stdio child of its gateway. That cannot work
here, for two independent reasons:

* aw-mcp-gateway mounts ``$AW_APPS_ROOT`` **read-only**. workspace-mcp needs a
  writable credentials directory, and the only writable path inside that
  container is the gateway's own app-data mount.
* The OAuth consent callback is served by the MCP process itself. As a stdio
  child it would bind a port inside the gateway's container, where nothing —
  not a browser, not this app — can reach it.

Running it as this app's own managed service in the workspace container fixes
both, and the gateway then talks to it over the container network by hostname,
exactly like aw-app-notion's ``aw-kanban`` entry does.

The server name is ``google-workspace`` so gateway-prefixed tool names come out
as ``aw__google_workspace__*`` — the same names agents already learned from the
monolith's ``aw_google_workspace__*``.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

from . import server_config

SERVER_NAME = "google-workspace"


def server_url(config: dict, host: str | None = None) -> str:
    """``socket.gethostname()`` is this container's own name, which is exactly
    the alias sibling containers resolve it by (ContainerSupervisor injects it
    as ``AW_WORKSPACE_HOST``) — nothing has to be provisioned for the gateway
    to reach us."""
    return f"http://{host or socket.gethostname()}:{server_config.port_of(config)}/mcp"


def build_mcp_servers(config: dict, *, configured: bool, host: str | None = None) -> dict:
    """Empty until an OAuth client is saved.

    Advertising an unconfigured server would give every agent 80-odd tools that
    all fail the same way at call time; an absent server at least fails where
    someone can see it (Settings, ``doctor``). Same call aw-app-notion makes
    with its token.
    """
    if not configured:
        return {}
    return {
        SERVER_NAME: {
            "type": "http",
            "url": server_url(config, host),
            "enabled": True,
        }
    }


def write_mcp_json(package_dir: str, config: dict, *, configured: bool,
                   host: str | None = None) -> dict:
    """Regenerate ``<package_dir>/mcp.json``, skipping the write when the
    content is byte-identical to what is already on disk.

    The skip is not an optimisation. aw-mcp-gateway reloads on **mtime**, so an
    unconditional rewrite on every activate/settings-save is a reload loop, and
    each reload briefly drops every tool the gateway proxies — including those
    of whatever agent session triggered it.
    """
    doc = {"mcpServers": build_mcp_servers(config, configured=configured, host=host)}
    body = json.dumps(doc, indent=2) + "\n"
    path = Path(package_dir, "mcp.json")
    try:
        if path.read_text(encoding="utf-8") == body:
            return doc
    except FileNotFoundError:
        pass
    path.write_text(body, encoding="utf-8")
    return doc
