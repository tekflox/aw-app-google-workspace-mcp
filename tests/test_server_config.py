"""Settings → (env file, argv, mcp.json) — the whole derivation chain.

These are the failures that are invisible at install time and only show up as
"the agent has no Google tools", so they get tests rather than trust.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_workspace_mcp_app import mcp_config, server_config  # noqa: E402

MONOLITH_CONFIG = {
    "google_oauth_client_id": "622552112316-abc.apps.googleusercontent.com",
    "user_google_email": "fredericowu@gmail.com",
    "tools": "gmail drive calendar contacts docs sheets tasks",
    "port": 8010,
    "allowed_file_dirs": "/opt/aw-workspace/.tmp",
}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """paths.credentials_dir() creates directories — keep tests off the real
    workspace home."""
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path / "home"))


def test_defaults_match_the_monolith():
    """The ported values are the point of this app; a silent default drift
    would re-authorize against the wrong Google project."""
    assert server_config.tool_groups({}) == [
        "gmail", "drive", "calendar", "contacts", "docs", "sheets", "tasks"]
    assert server_config.port_of({}) == 8010
    assert server_config.user_email({}) == "fredericowu@gmail.com"


def test_unknown_tool_groups_are_dropped():
    """argparse rejects an unknown --tools value with a usage error, so the
    server would never start and the service would just look dead."""
    assert server_config.tool_groups({"tools": "gmail bogus calendar"}) == ["gmail", "calendar"]


def test_empty_tools_falls_back_rather_than_registering_everything():
    assert server_config.tool_groups({"tools": "   "}) == server_config.DEFAULT_TOOLS.split()
    assert server_config.tool_groups({"tools": "nonsense"}) == server_config.DEFAULT_TOOLS.split()


def test_comma_separated_tools_are_accepted():
    assert server_config.tool_groups({"tools": "gmail,calendar"}) == ["gmail", "calendar"]


def test_port_falls_back_on_garbage():
    assert server_config.port_of({"port": "not-a-number"}) == 8010
    assert server_config.port_of({"port": "9999"}) == 9999


def test_redirect_uri_defaults_to_localhost_on_the_configured_port():
    """The ported OAuth client is a Desktop app, which Google only allows
    localhost redirects for."""
    assert server_config.redirect_uri({"port": 8010}) == "http://localhost:8010/oauth2callback"
    assert server_config.redirect_uri(
        {"port": 8010, "oauth_redirect_uri": "https://x/cb"}) == "https://x/cb"


def test_env_carries_every_var_the_server_reads():
    env = server_config.build_env(MONOLITH_CONFIG, "GOCSPX-secret")
    assert env["GOOGLE_OAUTH_CLIENT_ID"] == MONOLITH_CONFIG["google_oauth_client_id"]
    assert env["GOOGLE_OAUTH_CLIENT_SECRET"] == "GOCSPX-secret"
    assert env["USER_GOOGLE_EMAIL"] == "fredericowu@gmail.com"
    assert env["ALLOWED_FILE_DIRS"] == "/opt/aw-workspace/.tmp"
    assert env["OAUTHLIB_INSECURE_TRANSPORT"] == "1"
    # Both spellings: the preferred one and the legacy one older releases of
    # workspace-mcp are the only readers of. pin_version is user-editable.
    assert env["WORKSPACE_MCP_CREDENTIALS_DIR"] == env["GOOGLE_MCP_CREDENTIALS_DIR"]
    assert env["WORKSPACE_MCP_PORT"] == env["PORT"] == "8010"


def test_env_omits_absent_credentials_rather_than_writing_empties():
    """An empty GOOGLE_OAUTH_CLIENT_ID is not the same as an unset one — the
    server's own is_configured() check tests presence."""
    env = server_config.build_env({}, None)
    assert "GOOGLE_OAUTH_CLIENT_ID" not in env
    assert "GOOGLE_OAUTH_CLIENT_SECRET" not in env


def test_env_file_is_shell_safe_and_not_world_readable(tmp_path):
    cfg = dict(MONOLITH_CONFIG, allowed_file_dirs="/tmp/a b:/tmp/c$d")
    path = server_config.write_env_file(str(tmp_path), cfg, "sec'ret\"with spaces")
    body = path.read_text()
    assert "export GOOGLE_OAUTH_CLIENT_SECRET=" in body
    # It holds a live OAuth client secret.
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # Sourcing it must reproduce the values byte-for-byte, quoting and all.
    import subprocess
    out = subprocess.run(
        ["bash", "-c", f'set -a; source "{path}"; '
                       'printf "%s\\n%s\\n" "$GOOGLE_OAUTH_CLIENT_SECRET" "$ALLOWED_FILE_DIRS"'],
        capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0] == "sec'ret\"with spaces"
    assert out[1] == "/tmp/a b:/tmp/c$d"


def test_service_command_goes_through_the_wrapper():
    """Never the venv binary directly — the wrapper is what unsets PYTHONPATH
    and sources the env file, and a direct invocation silently gets neither."""
    cmd = server_config.service_command("/apps/x", MONOLITH_CONFIG)
    assert cmd.startswith("bash scripts/run_server.sh ")
    assert "--transport streamable-http" in cmd
    assert "--single-user" in cmd
    assert "--tools gmail drive calendar contacts docs sheets tasks" in cmd
    # The secret must never reach a command line — it would be in every `ps`.
    assert "GOCSPX" not in cmd and "client_secret" not in cmd


def test_is_configured_needs_both_halves():
    assert not server_config.is_configured({}, None)
    assert not server_config.is_configured(MONOLITH_CONFIG, None)
    assert not server_config.is_configured({}, "secret")
    assert server_config.is_configured(MONOLITH_CONFIG, "secret")


def test_mcp_json_is_empty_until_an_oauth_client_exists(tmp_path):
    """An advertised-but-unconfigured server hands every agent ~80 tools that
    all fail identically at call time. Absent fails somewhere visible."""
    doc = mcp_config.write_mcp_json(str(tmp_path), MONOLITH_CONFIG, configured=False)
    assert doc == {"mcpServers": {}}
    assert json.loads((tmp_path / "mcp.json").read_text()) == {"mcpServers": {}}


def test_mcp_json_points_the_gateway_at_this_container(tmp_path):
    doc = mcp_config.write_mcp_json(str(tmp_path), MONOLITH_CONFIG,
                                    configured=True, host="wshost")
    entry = doc["mcpServers"]["google-workspace"]
    assert entry == {"type": "http", "url": "http://wshost:8010/mcp", "enabled": True}
    # Named so gateway-prefixed tools keep the aw__google_workspace__* shape
    # agents already learned from the monolith.
    assert mcp_config.SERVER_NAME == "google-workspace"


def test_mcp_json_write_is_skipped_when_unchanged(tmp_path):
    """The gateway reloads on mtime, and every reload briefly drops every tool
    it proxies — so an unconditional rewrite on each activate is a reload loop."""
    mcp_config.write_mcp_json(str(tmp_path), MONOLITH_CONFIG, configured=True, host="h")
    before = (tmp_path / "mcp.json").stat().st_mtime_ns
    mcp_config.write_mcp_json(str(tmp_path), MONOLITH_CONFIG, configured=True, host="h")
    assert (tmp_path / "mcp.json").stat().st_mtime_ns == before

    mcp_config.write_mcp_json(str(tmp_path), dict(MONOLITH_CONFIG, port=9001),
                              configured=True, host="h")
    assert (tmp_path / "mcp.json").stat().st_mtime_ns != before


def test_credentials_dir_is_durable_and_private(tmp_path, monkeypatch):
    """Tokens must outlive an app update (uninstall + install drops the package
    dir), so they belong under AW_WORKSPACE_HOME, not .data/."""
    from google_workspace_mcp_app import paths
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path / "wh"))
    d = paths.credentials_dir()
    assert str(d).startswith(str(tmp_path / "wh"))
    assert "/.data/" not in str(d)
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


# --- token health: present is not usable -------------------------------------

def _routes_helpers(tmp_path, monkeypatch):
    """Exercise the two helpers in routes.py without standing up FastAPI."""
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path / "wh"))
    import importlib
    from google_workspace_mcp_app import paths as paths_mod, routes as routes_mod
    importlib.reload(paths_mod)
    return routes_mod, paths_mod


def _write_token(paths_mod, email, scopes, refresh=True):
    import json as _json
    blob = {"scopes": scopes, "client_id": "c", "client_secret": "s",
            "token_uri": "https://oauth2.googleapis.com/token", "token": "t"}
    if refresh:
        blob["refresh_token"] = "r"
    (paths_mod.credentials_dir() / f"{email}.json").write_text(_json.dumps(blob))


def test_oauth_states_is_not_an_account(tmp_path, monkeypatch):
    """It lives in the credentials dir and is the server's CSRF-state store.
    Globbing *.json counted it, so merely *starting* a consent flow made the
    app report an authorized account."""
    routes_mod, paths_mod = _routes_helpers(tmp_path, monkeypatch)
    (paths_mod.credentials_dir() / "oauth_states.json").write_text("{}")
    _write_token(paths_mod, "someone@gmail.com",
                 ["https://www.googleapis.com/auth/calendar"])

    accounts = sorted(p.stem for p in paths_mod.credentials_dir().glob("*.json")
                      if "@" in p.stem)
    assert accounts == ["someone@gmail.com"]


def test_partial_grant_is_reported_as_unusable(tmp_path, monkeypatch):
    """Google's consent screen lists sensitive scopes as opt-in checkboxes.
    Clicking through without ticking them yields openid+userinfo only: the token
    exchange succeeds and the file is written, but every API call still demands
    re-authorization. Found live on 2026-08-18."""
    routes_mod, paths_mod = _routes_helpers(tmp_path, monkeypatch)
    partial = ["https://www.googleapis.com/auth/userinfo.email",
               "https://www.googleapis.com/auth/userinfo.profile", "openid"]
    _write_token(paths_mod, "partial@gmail.com", partial)

    blob_scopes = [s for s in partial
                   if "/auth/" in s
                   and not s.endswith(("userinfo.email", "userinfo.profile"))]
    assert blob_scopes == [], "openid has no /auth/ prefix; the filter must catch this"
