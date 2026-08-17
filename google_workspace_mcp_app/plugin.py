"""Entrypoint referenced by aw-app.json's ``runtime.entrypoint``
("google_workspace_mcp_app.plugin:GoogleWorkspaceMcpAppPlugin").

What ``activate()`` does, in order, and why the order matters:

1. **Install the private venv if it is missing.** An app update is uninstall +
   install, which takes ``.data/`` with it — so this is a self-heal, not a
   one-time setup step. See installer.py for the ``PYTHONPATH`` trap that makes
   a naive install silently produce a broken venv.
2. **Write ``.data/server.env``** from the config + the stored client secret.
   The service reads it at start; nothing downstream works before it exists.
3. **Register the managed service.** ``ctx.services`` (``service:manage``) owns
   the process — the runtime stops it on uninstall, so no orphan survives.
4. **Write ``mcp.json``.** Last, because it is the thing that makes agents start
   calling the server; advertising it before the process can start would give
   every agent a server that refuses connections.

Ported from agentic-workspace's ``aw-google-workspace`` entry in
``src/config/mcp.json``. The monolith ran this as a stdio child of its gateway;
see mcp_config.py for why that shape cannot be reproduced here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from . import installer, mcp_config, paths, routes as routes_mod, server_config

log = logging.getLogger("aw_apps.google-workspace-mcp")

SERVICE_ID = routes_mod.SERVICE_ID
SECRET_KEY = routes_mod.SECRET_KEY


class GoogleWorkspaceMcpAppPlugin:
    def __init__(self) -> None:
        self._registered = False

    # ------------------------------------------------------------------
    # lifecycle

    async def activate(self, ctx) -> None:
        self.ctx = ctx

        version = str(ctx.config.get("pin_version") or "").strip()
        install = await asyncio.to_thread(
            installer.ensure_installed, ctx.package_dir, version)
        if not install.get("ok"):
            # Not fatal on purpose: the app still mounts its routes and its
            # settings window, which is where someone can see *why* the server
            # is not running and retry with POST /install. An app that refuses
            # to activate just disappears from the UI with the reason in a log
            # nobody reads.
            log.error("google-workspace-mcp: server install failed: %s",
                      install.get("error"))

        ctx.routes.register(routes_mod.build_routes(ctx, self))

        start_cmd = server_config.service_command(ctx.package_dir, ctx.config)
        self._write_env(ctx)
        ctx.services.register(SERVICE_ID, start_cmd, autostart=True)
        self._registered = True

        doc = self._write_mcp_json(ctx)
        authorized = sorted(p.stem for p in paths.credentials_dir().glob("*.json"))
        log.info(
            "google-workspace-mcp activated: server=%s port=%s tools=%s "
            "mcp servers=%s authorized accounts=%s",
            installer.installed_version(ctx.package_dir) or "not installed",
            server_config.port_of(ctx.config),
            ",".join(server_config.tool_groups(ctx.config)),
            sorted(doc["mcpServers"]) or "none (no OAuth client configured)",
            authorized or "none — OAuth consent not completed yet",
        )

    async def on_config_saved(self, ctx) -> None:
        """Core calls this after ``ctx.config`` has been updated by
        ``POST /api/apps/google-workspace-mcp/config``. Every setting this app
        has feeds either the env file or the service argv, so all of them need
        the server restarted to take effect — there is no cheap subset."""
        self.ctx = ctx
        self.apply_config(ctx, restart=True)

    async def deactivate(self) -> None:
        log.info("google-workspace-mcp deactivated")

    # ------------------------------------------------------------------
    # helpers shared with routes.py

    def apply_config(self, ctx, *, restart: bool = False) -> dict:
        """Regenerate everything derived from settings + secret, optionally
        bouncing the server so the new values are actually live."""
        self._write_env(ctx)
        doc = self._write_mcp_json(ctx)
        result: dict = {"mcp_servers": sorted(doc["mcpServers"].keys())}
        if restart:
            result["service"] = self.restart_service(ctx)
        return result

    def restart_service(self, ctx) -> dict:
        if not self._registered:
            return {"restarted": False, "reason": "service not registered yet"}
        try:
            ctx.services.stop(SERVICE_ID)
        except Exception as exc:
            log.warning("google-workspace-mcp: stop before restart failed: %s", exc)
        try:
            state = ctx.services.start(SERVICE_ID)
        except Exception as exc:
            log.error("google-workspace-mcp: service failed to start: %s", exc)
            return {"restarted": False, "error": str(exc)}
        return {"restarted": True, **(state if isinstance(state, dict) else {})}

    async def save_core_config(self, ctx, updates: dict) -> dict:
        """Persist non-secret settings through core's own config route.

        An app cannot persist config by mutating ``ctx.config`` (it is a copy),
        and it must not import the host runtime. The supported path is an
        authenticated HTTP call back to the workspace API with this process's
        own ``AW_WORKSPACE_API_KEY`` — see the app template's
        docs/app-workspace-api-auth.md.
        """
        import httpx

        api_key = os.environ.get("AW_WORKSPACE_API_KEY")
        port = os.environ.get("AW_PORT") or "9030"
        url = f"http://127.0.0.1:{port}/api/apps/{ctx.app_id}/config"
        headers = {"X-Api-Key": api_key} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, json={"config": updates}, headers=headers)
            return {"ok": resp.status_code < 400, "status": resp.status_code}
        except Exception as exc:
            log.error("google-workspace-mcp: could not persist config via core: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------

    def _write_env(self, ctx) -> None:
        secret = None
        try:
            secret = ctx.secrets.read(SECRET_KEY)
        except Exception as exc:  # secrets facade unavailable — log, don't crash
            log.warning("google-workspace-mcp: could not read client secret: %s", exc)
        server_config.write_env_file(ctx.package_dir, ctx.config, secret)

    def _write_mcp_json(self, ctx) -> dict:
        secret = None
        try:
            secret = ctx.secrets.read(SECRET_KEY)
        except Exception:
            pass
        configured = server_config.is_configured(ctx.config, secret)
        return mcp_config.write_mcp_json(
            ctx.package_dir, ctx.config,
            configured=configured, host=socket.gethostname())
