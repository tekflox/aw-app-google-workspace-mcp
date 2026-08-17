# aw-app-google-workspace-mcp

Gmail, Drive, Calendar, Docs, Sheets, Tasks and Contacts for every agent in this
workspace, behind one Google OAuth token.

Wraps the community [`workspace-mcp`](https://github.com/taylorwilsdon/google_workspace_mcp)
server (PyPI `workspace-mcp`, MIT), runs it as a managed background service, and
registers it with MCP Gateway. 86 tools, gateway-prefixed `aw__google_workspace__*`.

Ported from agentic-workspace's `aw-google-workspace` MCP entry (2026-08-17).

## Install

```bash
aw-workspace-cli marketplace install google-workspace-mcp
```

Then open **Google Workspace MCP** in the Apps grid and save an OAuth client
(id + secret). Nothing is advertised to the gateway until you do — see
"Configuration" below for why.

## Configuration

| Setting | Default | Notes |
|---|---|---|
| `google_oauth_client_id` | — | Plain config. Not a credential on its own. |
| `google_oauth_client_secret` | — | Zero-knowledge secret store, via `POST /settings`. Never plain config. |
| `user_google_email` | `fredericowu@gmail.com` | `--single-user` fallback for every tool call. |
| `tools` | `gmail drive calendar contacts docs sheets tasks` | Also available: `slides`, `forms`, `chat`, `appscript`, `search`. |
| `port` | `8010` | Container-internal only; nothing is published to the host. |
| `allowed_file_dirs` | `/opt/aw-workspace/.tmp` | Where `send_gmail_message` may read attachments from by `path`. |
| `oauth_redirect_uri` | — | Leave empty for `http://localhost:<port>/oauth2callback`, which the ported Desktop-app OAuth client expects. |
| `pin_version` | `1.24.1` | PyPI version installed into this app's private venv. |

With no OAuth client saved, the app writes an **empty** `mcpServers` on purpose.
Advertising an unconfigured server hands every agent ~86 tools that all fail
identically at call time; absent fails somewhere a human can see it.

Settings changes restart the server automatically — the process reads its
environment at start, so there is no hot-reload path.

## Authorizing a Google account

A saved OAuth client is not consent. Two paths:

1. **Import an existing token** (no browser needed). The monolith keeps them at
   `.tmp/google_workspace_mcp_credentials/<email>.json`:
   ```bash
   curl -sS -X POST http://127.0.0.1:9030/api/apps/google-workspace-mcp/credentials \
     -H "X-Api-Key: $AW_WORKSPACE_API_KEY" -H 'Content-Type: application/json' \
     -d "{\"email\": \"you@gmail.com\", \"credentials\": $(cat you@gmail.com.json)}"
   ```
2. **Fresh consent** — any agent calls `start_google_auth(service_name="Gmail")`,
   which returns a Google consent URL a human must open. The callback lands on
   this server's own `/oauth2callback`, so the browser completing it has to be
   able to reach that port.

Tokens live in `$AW_WORKSPACE_HOME/data/google-workspace-mcp/credentials/` and
survive an app update.

## Routes

All under `/api/apps/google-workspace-mcp`, behind the workspace IdentityGuard.

| Route | Purpose |
|---|---|
| `GET /status` | Everything the settings window binds to: configured, authorized accounts, service state, mcp url. |
| `POST /settings` | Save the OAuth client (secret → secret store), regenerate, restart. |
| `POST /logout` | Forget the OAuth client. Does **not** delete tokens. |
| `POST /install` | Force-reinstall the private venv (after changing `pin_version`). |
| `POST /restart` | Bounce the managed server. |
| `GET /logs` | The service's captured stdout/stderr — the only log source a Tier-1 managed service has. |
| `GET/POST /credentials`, `DELETE /credentials/{email}` | List / import / remove Google tokens. |
| `GET /mcp.json` | What the gateway will see, without shelling into the container. |

## Architecture

See [`docs/architecture/aw-app-google-workspace-mcp.md`](docs/architecture/aw-app-google-workspace-mcp.md)
for why this is an HTTP managed service instead of the monolith's stdio child,
and the two `PYTHONPATH` traps that make a naive install silently produce a
broken venv.

## Tests

```bash
python3 tests/validate_manifest.py aw-app.json
python3 -m pytest tests -q
```
