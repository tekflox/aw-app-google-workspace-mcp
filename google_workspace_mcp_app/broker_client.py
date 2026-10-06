"""HTTP client to aw-backend's shared Google OAuth broker —
``/api/workspaces/{slug}/google-oauth/{start,redeem}``.

Mirrors ``aw-app-secrets/secrets_app/backend_client.py`` exactly: auth is
this workspace's OWN host credential, ``AW_WORKSPACE_HOST_TOKEN`` (an
``awlk_`` token minted by the aw-remote-host ``/link`` handshake, already
sitting in this process's environment — no new credential needed). That
token is accepted by aw-backend's ``require_workspace_actor`` and scoped to
exactly this workspace, the same mechanism ``aw-app-secrets`` already relies
on and ``CloudRegistry`` has used since F3.

A workspace with no cloud link (no ``AW_WORKSPACE_HOST_TOKEN``) has no
broker to talk to — ``configured`` below is how ``routes.py`` decides
whether to offer the zero-config "Connect Google Account" path at all, or
fall back to the BYO-client flow that has worked since this app shipped.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("aw_apps.google-workspace-mcp")

DEFAULT_TIMEOUT = 20.0


class BrokerUnavailable(RuntimeError):
    """The workspace has no cloud link, so there is no OAuth broker to talk to."""


class BrokerRequestFailed(RuntimeError):
    """aw-backend answered a call with a non-2xx status.

    Carries the real ``status_code`` and JSON ``detail`` aw-backend sent,
    same reasoning as ``aw-app-secrets``'s own ``BackendRequestFailed`` — a
    bare ``raise_for_status()`` would surface only httpx's generic "Client
    error '400 Bad Request' for url '...'", never the reason a human (or the
    next debugging session) actually needs.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"aw-backend ({status_code}): {detail}")


def _response_detail(r: httpx.Response) -> str:
    try:
        detail = r.json().get("detail") or r.json().get("error")
    except Exception:
        detail = None
    return detail if detail else r.text[:300]


class GoogleOAuthBroker:
    def __init__(self, backend_url: str | None = None, workspace: str | None = None,
                 token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.backend_url = (backend_url or os.environ.get("AW_BACKEND_URL", "")).rstrip("/")
        self.workspace = workspace or os.environ.get("AW_WORKSPACE", "")
        self.token = token or os.environ.get("AW_WORKSPACE_HOST_TOKEN", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.backend_url and self.token and self.workspace)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _base(self) -> str:
        return f"{self.backend_url}/api/workspaces/{self.workspace}/google-oauth"

    def _require(self) -> None:
        if not self.configured:
            raise BrokerUnavailable(
                "no cloud link: AW_BACKEND_URL, AW_WORKSPACE and AW_WORKSPACE_HOST_TOKEN must "
                "all be set. A BYOD workspace that never completed the aw-remote-host /link "
                "handshake has no shared OAuth broker to reach — use a BYO client instead."
            )

    def start(self, scopes: list[str], return_url: str) -> str:
        """Ask aw-backend to start a Google consent round-trip for this
        workspace. Returns the ``authorize_url`` to redirect the browser to —
        this call itself is server-to-server, the actual browser redirect is
        the caller's job (``routes.py``'s ``GET /oauth/start``)."""
        self._require()
        r = httpx.post(f"{self._base()}/start",
                       json={"scopes": scopes, "return_url": return_url},
                       headers=self._headers(), timeout=self.timeout)
        if r.is_error:
            raise BrokerRequestFailed(r.status_code, _response_detail(r))
        return r.json()["authorize_url"]

    def redeem(self, ticket: str) -> dict:
        """Exchange a one-time ticket (handed to the browser on the
        ``…/oauth/finish?ticket=…`` redirect) for the shared client
        credentials plus the full Google token response. One-shot: a second
        redeem of the same ticket fails."""
        self._require()
        r = httpx.post(f"{self._base()}/redeem",
                       json={"ticket": ticket},
                       headers=self._headers(), timeout=self.timeout)
        if r.is_error:
            raise BrokerRequestFailed(r.status_code, _response_detail(r))
        return r.json()
