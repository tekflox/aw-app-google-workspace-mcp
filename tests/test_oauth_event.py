"""``_emit_oauth_event`` — the ``ctx.notify.event()`` push fired whenever an
account's authorized state changes out-of-band (cloud-broker consent
finishing, the BYO dead-tab relay, importing/deleting a token file), so an
open Settings window refreshes its Google account status without the user
closing and reopening the panel. See the Architect's design on Kanban card
3f15bf3b for the full WS-push mechanism this feeds (aw-workspace core's
``NotificationManager.emit_app_event`` / ``ctx.notify.event``).

No live aw-backend or real Google consent is exercised here — same
ad-hoc-TestClient-with-a-hand-built-ctx approach this repo's routes.py has
always needed, since it has no standalone mode.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_workspace_mcp_app import routes  # noqa: E402


class _FakeSecrets:
    def __init__(self):
        self._store = {}

    def read(self, key):
        return self._store.get(key)

    def write(self, key, value):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)


class _FakeServices:
    def status(self, service_id):
        return {"running": False}

    def start(self, service_id):
        return {"running": True}

    def stop(self, service_id):
        return {"running": False}


class _FakeCtx:
    def __init__(self):
        self.app_id = "google-workspace-mcp"
        self.config = {}
        self.secrets = _FakeSecrets()
        self.services = _FakeServices()
        self.notify = MagicMock()
        self.notify.event = MagicMock()
        self.package_dir = "/tmp/google-workspace-mcp-test-pkg"


class _FakePlugin:
    def __init__(self):
        self.apply_config = MagicMock(return_value={"mcp_servers": []})
        self.restart_service = MagicMock(return_value={"restarted": True})
        self.save_core_config = AsyncMock(return_value={})


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path / "home"))


@pytest.fixture()
def ctx():
    return _FakeCtx()


@pytest.fixture()
def plugin():
    return _FakePlugin()


@pytest.fixture()
def client(ctx, plugin):
    app = routes.build_routes(ctx, plugin)
    return TestClient(app)


class _FakeBroker:
    """Stand-in for GoogleOAuthBroker — the real one calls aw-backend."""

    result: dict = {}

    def __init__(self, *a, **k):
        pass

    def redeem(self, ticket):
        return self.result


def _write_credentials(home, email, usable=True):
    creds_dir = Path(home) / "data" / "google-workspace-mcp" / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"] if usable else ["openid"]
    blob = {"refresh_token": "rt" if usable else None, "scopes": scopes,
            "token": "t", "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "cid", "client_secret": "secret"}
    (creds_dir / f"{email}.json").write_text(json.dumps(blob), encoding="utf-8")
    return creds_dir


class TestOauthFinish:
    def test_usable_grant_emits_app_event_and_toast(self, isolated_home, ctx, plugin, client, monkeypatch):
        _FakeBroker.result = {
            "client_id": "cid123",
            "client_secret": "secret123",
            "email": "user@example.com",
            "tokens": {
                "access_token": "at", "refresh_token": "rt",
                "scope": "https://www.googleapis.com/auth/gmail.readonly openid",
                "expires_in": 3600,
            },
        }
        monkeypatch.setattr(routes, "GoogleOAuthBroker", _FakeBroker)

        resp = client.get("/oauth/finish", params={"ticket": "tok123"})
        assert resp.status_code == 200
        assert "connected" in resp.text.lower()

        ctx.notify.event.assert_called_once_with(
            "oauth_completed", {"email": "user@example.com", "usable": True})
        ctx.notify.assert_called_once()
        assert ctx.notify.call_args.kwargs.get("level") == "success"

    def test_partial_grant_still_emits_app_event_with_usable_false(self, isolated_home, ctx, plugin, client, monkeypatch):
        _FakeBroker.result = {
            "client_id": "cid123",
            "client_secret": "secret123",
            "email": "partial@example.com",
            "tokens": {
                "access_token": "at", "refresh_token": "rt",
                "scope": "openid", "expires_in": 3600,
            },
        }
        monkeypatch.setattr(routes, "GoogleOAuthBroker", _FakeBroker)

        resp = client.get("/oauth/finish", params={"ticket": "tok456"})
        assert resp.status_code == 200
        assert "tick every permission checkbox" in resp.text

        ctx.notify.event.assert_called_once_with(
            "oauth_completed", {"email": "partial@example.com", "usable": False})
        assert ctx.notify.call_args.kwargs.get("level") == "warning"


class TestRelayOauthCallback:
    def test_ok_relay_emits_app_event_for_the_newly_authorized_account(self, isolated_home, ctx, plugin, client, monkeypatch):
        home = ctx.secrets  # not used for creds, just keeping fixture order obvious
        home_dir = Path(__import__("os").environ["AW_WORKSPACE_HOME"])

        class _FakeResp:
            status_code = 200
            text = "ok"

        class _FakeAsyncClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, params=None):
                # Simulate the upstream server writing the token file as a
                # side effect of this call, same as the real /oauth2callback.
                _write_credentials(home_dir, "relayed@example.com", usable=True)
                return _FakeResp()

        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        resp = client.post("/oauth-callback", json={
            "url": "http://localhost:8010/oauth2callback?code=abc&state=xyz",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        ctx.notify.event.assert_called_once_with(
            "oauth_completed", {"email": "relayed@example.com", "usable": True})

    def test_declined_consent_emits_nothing(self, isolated_home, ctx, plugin, client):
        resp = client.post("/oauth-callback", json={
            "url": "http://localhost:8010/oauth2callback?error=access_denied",
        })
        assert resp.status_code == 400
        ctx.notify.event.assert_not_called()


class TestImportAndDeleteCredentials:
    def test_import_credentials_emits_app_event(self, isolated_home, ctx, plugin, client):
        resp = client.post("/credentials", json={
            "email": "imported@example.com",
            "credentials": {
                "refresh_token": "rt",
                "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
            },
        })
        assert resp.status_code == 200
        ctx.notify.event.assert_called_once_with(
            "oauth_completed", {"email": "imported@example.com", "usable": True})

    def test_delete_credentials_emits_app_event_with_usable_false(self, isolated_home, ctx, plugin, client):
        home_dir = Path(__import__("os").environ["AW_WORKSPACE_HOME"])
        _write_credentials(home_dir, "todelete@example.com", usable=True)

        resp = client.delete("/credentials/todelete@example.com")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        ctx.notify.event.assert_called_once_with(
            "oauth_completed", {"email": "todelete@example.com", "usable": False})

    def test_delete_of_nonexistent_credentials_emits_nothing(self, isolated_home, ctx, plugin, client):
        resp = client.delete("/credentials/never-existed@example.com")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False
        ctx.notify.event.assert_not_called()
