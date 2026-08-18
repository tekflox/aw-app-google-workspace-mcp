"""This app's backend sub-app, mounted by the runtime at
``/api/apps/google-workspace-mcp`` behind the workspace's IdentityGuard.

There is no standalone mode: every route needs ``ctx.secrets`` (the OAuth
client secret), ``ctx.config`` (everything else) and ``ctx.services`` (the
managed server), none of which exist outside the real app runtime. Same shape
as aw-app-notion and aw-app-git.

Settings are split across two endpoints on purpose, because the runtime treats
them differently:

* the OAuth client **secret** goes to ``ctx.secrets`` through ``POST /settings``
  here. The generic config path would land it in plain, cloud-syncable app
  config. The ``x-secret`` flag on ``google_oauth_client_secret`` in the
  manifest exists only so the settings UI renders a password field.
* everything else goes to core's own ``POST /api/apps/google-workspace-mcp/
  config``. That is the *only* path that persists app config —
  ``ctx.config`` handed to a plugin is a copy, so writing to it from an app
  route changes nothing that survives the request. The plugin's
  ``on_config_saved`` hook is what re-applies the result.

Same reasoning as aw-app-notion, which routes its token to ``/settings`` and
its Kanban database id through the generic path.
"""
from __future__ import annotations

import json
import logging

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from . import installer, mcp_config, paths, server_config

log = logging.getLogger("aw_apps.google-workspace-mcp")

SECRET_KEY = "google_oauth_client_secret"
SERVICE_ID = "workspace-mcp"


def build_routes(ctx, plugin) -> FastAPI:
    app = FastAPI(title="google-workspace-mcp")

    def _secret() -> str | None:
        return ctx.secrets.read(SECRET_KEY)

    def _configured() -> bool:
        return server_config.is_configured(ctx.config, _secret())

    def _authorized_accounts() -> list[str]:
        """Google accounts with a token file on disk.

        ``oauth_states.json`` lives in the same directory and is the server's
        in-flight CSRF-state store, not an account — globbing ``*.json`` blindly
        reported it as one, so a workspace that had merely *started* a consent
        flow looked authorized. Filtering on "@" is enough: token files are
        named after the email address.
        """
        try:
            return sorted(p.stem for p in paths.credentials_dir().glob("*.json")
                          if "@" in p.stem)
        except OSError:
            return []

    def _token_health(email: str) -> dict:
        """Whether a token file is actually *usable*, which is not the same as
        present — and the difference cost a whole debugging session.

        Two ways a token on disk is dead weight, both of which look identical to
        a good one until a tool call fails:

        * Google no longer honours the refresh token (``invalid_grant``). An
          OAuth app in `Testing` publishing status expires refresh tokens after
          7 days, so this is the *normal* end state, not an edge case.
        * The grant is **partial**. Google's consent screen lists sensitive
          scopes as opt-in checkboxes; clicking through without ticking them
          yields a token carrying only ``openid``/``userinfo``. The exchange
          succeeds, the file is written, and every real API call still demands
          re-authorization.

        Reported per account rather than folded into one boolean, because the
        two need different fixes: re-consent vs re-consent *and tick the boxes*.
        """
        path = paths.credentials_dir() / f"{email}.json"
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"usable": False, "reason": f"unreadable: {exc}"}

        scopes = [s for s in (blob.get("scopes") or [])
                  if "/auth/" in s and not s.endswith(("userinfo.email", "userinfo.profile"))]
        if not scopes:
            return {"usable": False, "reason": "partial grant — only openid/userinfo "
                                               "were approved; re-consent and tick every "
                                               "permission checkbox",
                    "scope_count": 0}
        if not blob.get("refresh_token"):
            return {"usable": False, "reason": "no refresh_token — the grant was not offline",
                    "scope_count": len(scopes)}
        return {"usable": True, "scope_count": len(scopes)}

    def _service_state() -> dict:
        try:
            return ctx.services.status(SERVICE_ID)
        except Exception as exc:  # service not registered yet (early boot)
            return {"running": False, "error": str(exc)}

    @app.get("/status")
    async def status() -> dict:
        accounts = _authorized_accounts()
        configured = _configured()
        health = {e: _token_health(e) for e in accounts}
        usable = [e for e, h in health.items() if h.get("usable")]
        return {
            # "logged_in" is what windows/main.json's auth_status widget binds
            # to. It tracks a *usable* token, not merely a file on disk: a dead
            # or partially-granted token reported as logged-in is precisely how
            # this app looked healthy for a day while every call failed.
            "logged_in": bool(usable),
            "configured": configured,
            "usable_accounts": usable,
            "token_health": health,
            "client_id": str(ctx.config.get("google_oauth_client_id") or ""),
            "client_secret_saved": bool(_secret()),
            "authorized_accounts": accounts,
            "default_email": server_config.user_email(ctx.config),
            "tools": server_config.tool_groups(ctx.config),
            "port": server_config.port_of(ctx.config),
            "oauth_redirect_uri": server_config.redirect_uri(ctx.config),
            "credentials_dir": str(paths.credentials_dir()),
            "server_installed": installer.is_installed(ctx.package_dir),
            "server_version": installer.installed_version(ctx.package_dir),
            "service": _service_state(),
            "mcp_server_enabled": bool(
                mcp_config.build_mcp_servers(ctx.config, configured=configured)),
            "mcp_url": mcp_config.server_url(ctx.config),
        }

    @app.post("/settings")
    async def save_settings(data: dict = Body(...)) -> dict:
        """Store the OAuth client secret, then re-apply everything downstream of
        it — the env file, mcp.json, and a server restart.

        The restart is not optional politeness: the secret is read from the env
        file at process start, so a save that left the old process running would
        report success while the server still holds the previous credentials.

        The client *id* is accepted here too as a convenience — the settings
        form asks for both halves of an OAuth client in one place, and asking a
        user to save one field here and its twin somewhere else is how a
        half-configured client happens. It is forwarded to core's config route,
        not written to ``ctx.config``, which would not persist.
        """
        secret = (data.get(SECRET_KEY) or "").strip()
        if not secret and not (data.get("google_oauth_client_id") or "").strip():
            return JSONResponse(
                {"ok": False, "error": f"{SECRET_KEY} or google_oauth_client_id is required"},
                status_code=400)
        if secret:
            ctx.secrets.write(SECRET_KEY, secret)

        client_id = (data.get("google_oauth_client_id") or "").strip()
        forwarded = None
        if client_id and client_id != str(ctx.config.get("google_oauth_client_id") or ""):
            forwarded = await plugin.save_core_config(ctx, {"google_oauth_client_id": client_id})

        result = plugin.apply_config(ctx, restart=True)
        return {"ok": True, "configured": _configured(),
                "client_id_saved": forwarded, **result}

    @app.post("/logout")
    async def logout() -> dict:
        """Forget the OAuth client. Deliberately does NOT delete the token
        files — dropping a client secret is a config decision, throwing away a
        human's Google consent is not, and the two are undone very differently.
        Use DELETE /credentials/{email} for that."""
        ctx.secrets.delete(SECRET_KEY)
        plugin.apply_config(ctx, restart=True)
        return {"ok": True, "logged_in": False, "configured": False}

    @app.post("/install")
    async def install_server() -> dict:
        """Force a (re)install of the private venv — used after changing
        `pin_version`, or to recover an app dir that lost `.data/`."""
        version = str(ctx.config.get("pin_version") or "").strip()
        result = installer.install(ctx.package_dir, version)
        if result.get("ok"):
            plugin.apply_config(ctx, restart=True)
            return result
        return JSONResponse(result, status_code=500)

    @app.post("/restart")
    async def restart() -> dict:
        return plugin.restart_service(ctx)

    @app.get("/logs")
    async def logs() -> dict:
        """The managed service's captured stdout/stderr backlog — the only log
        source a Tier-1 managed service has (no container log driver)."""
        state = _service_state()
        return {"service": SERVICE_ID, "lines": state.get("log_lines") or [],
                "running": bool(state.get("running"))}

    @app.get("/credentials")
    async def list_credentials() -> dict:
        return {"dir": str(paths.credentials_dir()),
                "accounts": _authorized_accounts()}

    @app.post("/oauth-callback")
    async def relay_oauth_callback(data: dict = Body(...)) -> dict:
        """Finish a consent flow whose redirect could not be delivered.

        The ported OAuth client is a Google **Desktop app** client, and Google
        only allows those to redirect to `localhost`/`127.0.0.1`. "localhost"
        means *the workspace container*, so when a human completes consent in a
        browser on their own machine the redirect lands nowhere: the tab shows
        a connection error.

        That failed page is not a dead end — the authorization code is sitting
        in its URL bar. Paste the whole URL here and this route replays it
        against the server's own callback, over loopback, where it does resolve.
        Everything after that (token exchange, writing `<email>.json`) is the
        upstream server's normal flow, untouched.

        Body: {"url": "http://localhost:8010/oauth2callback?code=…&state=…"} —
        or {"code": "…", "state": "…"} if you'd rather pull the parts out
        yourself. The host and port in a pasted URL are ignored; only the query
        string is used, so a redirect that arrived on any port still works.
        """
        import urllib.parse

        import httpx

        raw_url = (data.get("url") or "").strip()
        if raw_url:
            query = urllib.parse.urlparse(raw_url).query
            params = dict(urllib.parse.parse_qsl(query))
        else:
            params = {k: str(data[k]) for k in ("code", "state", "scope")
                      if data.get(k)}

        if params.get("error"):
            # Google reports a declined consent in the redirect, not as an HTTP
            # error — replaying it would just log a confusing failure.
            return JSONResponse(
                {"ok": False, "error": f"Google returned '{params['error']}' — "
                                       "consent was declined or cancelled."},
                status_code=400)
        if not params.get("code"):
            return JSONResponse(
                {"ok": False,
                 "error": "no authorization code found — paste the full "
                          "redirect URL, including its ?code=… query string"},
                status_code=400)

        port = server_config.port_of(ctx.config)
        target = f"http://127.0.0.1:{port}/oauth2callback"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(target, params=params)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"could not reach the local server at {target}: {exc}"},
                status_code=502)

        accounts = _authorized_accounts()
        ok = resp.status_code < 400 and bool(accounts)
        log.info("google-workspace-mcp: oauth callback relayed, status=%s accounts=%s",
                 resp.status_code, accounts)
        return {
            "ok": ok,
            "callback_status": resp.status_code,
            "authorized_accounts": accounts,
            # The server renders an HTML page; surface a trimmed body so a
            # failure says something more useful than a bare status code.
            "detail": resp.text[:400] if not ok else None,
        }

    @app.post("/credentials")
    async def import_credentials(data: dict = Body(...)) -> dict:
        """Import an existing workspace-mcp token file instead of re-consenting.

        This is the migration path from agentic-workspace, whose tokens live in
        `.tmp/google_workspace_mcp_credentials/<email>.json` on the monolith
        host. Google's consent screen needs a human and a browser that can
        reach the callback; a token that already exists needs neither.

        Body: {"email": "...", "credentials": {...}} — `credentials` may be the
        parsed object or the raw file text.
        """
        email = (data.get("email") or "").strip()
        blob = data.get("credentials")
        if not email or blob in (None, ""):
            return JSONResponse(
                {"ok": False, "error": "email and credentials are required"},
                status_code=400)
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except json.JSONDecodeError as exc:
                return JSONResponse({"ok": False, "error": f"credentials is not valid JSON: {exc}"},
                                    status_code=400)
        if not isinstance(blob, dict) or "refresh_token" not in blob:
            return JSONResponse(
                {"ok": False,
                 "error": "credentials must be a workspace-mcp token object with a refresh_token"},
                status_code=400)

        path = paths.credentials_dir() / f"{email}.json"
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        path.chmod(0o600)
        log.info("google-workspace-mcp: imported credentials for %s", email)
        # The server reads the credentials dir at call time, but a restart also
        # clears any cached "this account is unauthenticated" state.
        plugin.restart_service(ctx)
        return {"ok": True, "email": email, "accounts": _authorized_accounts()}

    @app.delete("/credentials/{email}")
    async def delete_credentials(email: str) -> dict:
        path = paths.credentials_dir() / f"{email}.json"
        existed = path.exists()
        if existed:
            path.unlink()
        plugin.restart_service(ctx)
        return {"ok": True, "deleted": existed, "accounts": _authorized_accounts()}

    @app.get("/mcp.json")
    async def mcp_json() -> dict:
        """The same document this app wrote to disk — lets you check what the
        gateway will see without a shell into the container."""
        return {"mcpServers": mcp_config.build_mcp_servers(
            ctx.config, configured=_configured())}

    return app
