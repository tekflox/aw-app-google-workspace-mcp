---
name: aw-google-workspace
description: Full Google Workspace access (Gmail, Drive, Calendar, Docs, Sheets, Tasks, Contacts) via the community "workspace-mcp" server (taylorwilsdon/google_workspace_mcp), exposed as google-workspace on the aw-gateway by the aw-app-google-workspace-mcp app. This is the ONLY Calendar/Contacts/Gmail/Drive/Docs/Sheets/Tasks MCP in this workspace. Use for any calendar, email, drive, docs, sheets, tasks, or contacts request.
---

# aw-google-workspace — community Google Workspace MCP

Wraps `taylorwilsdon/google_workspace_mcp` (PyPI: `workspace-mcp`, MIT
license) — the most complete open Google Workspace MCP, covering Gmail,
Drive, Calendar, Docs, Sheets, Tasks, Contacts in **one server, one OAuth
token**, instead of one hand-written server per API.

Repo: https://github.com/taylorwilsdon/google_workspace_mcp
Ported from agentic-workspace's `aw-google-workspace` (2026-08-17). The
monolith's own skill of the same name is the ancestor of this file; the
sections that describe *the monolith's* plumbing were rewritten, not copied —
see "How this runs here" below, which is genuinely different.

## Tool set (gateway-prefixed `aw__google_workspace__*`)

Registered with `--tools gmail drive calendar contacts docs sheets tasks`
(no `slides`, `forms`, `chat`, `appscript`, `search` — add them in the app's
Settings → `tools`, then restart the server). Highlights:

- **Gmail**: `search_gmail_messages`, `get_gmail_message_content`,
  `send_gmail_message`, `draft_gmail_message`, `list_gmail_labels`,
  `modify_gmail_message_labels`, `manage_gmail_filter`
- **Drive**: `search_drive_files`, `get_drive_file_content`,
  `create_drive_file`, `update_drive_file`, `set_drive_file_permissions`,
  `get_drive_shareable_link`
- **Calendar**: `list_calendars`, `get_events`, `manage_event` (create/
  update/delete in one tool), `query_freebusy`
- **Contacts**: `list_contacts`, `search_contacts`, `manage_contact`,
  `manage_contact_group`
- **Docs/Sheets**: `create_doc`/`create_spreadsheet`, `get_doc_as_markdown`,
  `read_sheet_values`/`modify_sheet_values`, plus formatting tools
- **Tasks**: `list_tasks`, `manage_task`, `list_task_lists`

Every tool takes `user_google_email` and defaults to the app's
`user_google_email` setting (the server runs `--single-user`). Full upstream
docs: `gh api repos/taylorwilsdon/google_workspace_mcp/contents/README.md -H
"Accept: application/vnd.github.raw"`.

## If a tool call says authorization is needed

Call `start_google_auth(service_name="<Gmail|Drive|Calendar|...>")` — it
returns a Google OAuth consent URL that **a human with the Google account has
to open**. After consent the browser is redirected to the server's own
`/oauth2callback`, which is served by the same process on the app's configured
port (default 8010) inside the workspace container.

That last part is the catch, and it is worth being honest about rather than
burning a run on it: the consent URL opens in *someone's* browser, and that
browser has to be able to reach that callback. From a laptop it cannot, unless
the port is tunnelled. Before starting a consent flow, prefer the path that
needs no browser at all:

**Import an existing token.** If the account was already authorized in
agentic-workspace, its token file is on that host at
`.tmp/google_workspace_mcp_credentials/<email>.json`. Post it straight in:

```bash
curl -sS -X POST http://127.0.0.1:9030/api/apps/google-workspace-mcp/credentials \
  -H "X-Api-Key: $AW_WORKSPACE_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"email\": \"someone@gmail.com\", \"credentials\": $(cat someone@gmail.com.json)}"
```

Tokens are refresh tokens — they keep working after the move. Check what is
authorized with `GET /api/apps/google-workspace-mcp/status`
(`authorized_accounts`).

## How this runs here (differs from the monolith)

The monolith ran `workspace-mcp` as a **stdio child of its MCP gateway**. That
shape does not survive the port, for two independent reasons:

- aw-mcp-gateway mounts `$AW_APPS_ROOT` **read-only**, and the server needs a
  writable credentials directory.
- The OAuth callback is served by the MCP process itself. As a stdio child it
  would bind a port inside the *gateway's* container, where nothing can reach
  it — not a browser, not the app.

So here it is a **managed service of the `google-workspace-mcp` app**
(`ctx.services`, capability `service:manage`), running
`--transport streamable-http` in the workspace container, and the app writes an
**http** entry into its own `mcp.json` pointing the gateway at
`http://<workspace-host>:<port>/mcp`. Same trick as aw-app-notion's `aw-kanban`
server.

Consequences worth knowing:

- **Private venv, never the shared one.** `workspace-mcp` pins its own
  `starlette`/`cryptography`/`urllib3`/`mcp`/`fastmcp`. In the monolith,
  installing it into the shared venv upgraded those under every other Python
  MCP server and had to be unpicked by hand. It lives in
  `<app dir>/.data/venv` and is rebuilt on activate if missing.
- **`PYTHONPATH` must be scrubbed to install or run it.** This container
  exports `PYTHONPATH` pointing at the shared venv's site-packages, and a venv
  does *not* override it — it is searched first. Symptoms if you forget:
  `pip install` inside the fresh venv sees fastapi/requests/urllib3 as already
  satisfied and installs **none of them**, exits 0, and the server dies at
  first start on `ModuleNotFoundError: requests`. `scripts/run_server.sh` and
  `installer.py` both unset it; anything you run by hand must too
  (`env -u PYTHONPATH …`).
- **Credentials are durable, the venv is not.** Tokens live under
  `$AW_WORKSPACE_HOME/data/google-workspace-mcp/credentials/` so an app update
  (which is uninstall + install, and takes the package dir with it) cannot lose
  a human's Google consent. The venv is disposable and self-heals.
- **No MCP server entry until an OAuth client is saved.** With no client id +
  secret the app writes an empty `mcpServers`, so the tools are absent rather
  than present-and-all-failing.
- **Settings that change anything need a restart**, which the app does for you
  on save. There is no hot-reload path — the process reads its env at start.

## Sending attachments — use `path`, NOT `content` (base64)

`send_gmail_message`'s `attachments` param accepts `path`, `url`, or
`content` (base64). **Always use `path`.** Base64-encoding a file and pasting
it into `content` means emitting the entire blob as literal tokens in a tool
call — for a ~50-150KB image that is tens of thousands of tokens, and it has
caused an agent to hang mid-call. Worse, it is silently corruption-prone: one
such run sent an email with a truncated JPG (decoded bytes did not end in the
JPEG EOI marker `FF D9`). Gmail's API does not validate attachment bytes, so a
truncated image sends "successfully" and only looks broken when a human opens
it.

`path` only works inside the directories the **server process** is allowed to
read — the app's `allowed_file_dirs` setting (`ALLOWED_FILE_DIRS`), which
defaults to `/opt/aw-workspace/.tmp`. Reads outside raise `"local file access
is limited to the server's permitted directories"`, which reads like a
missing-tool problem but is not: the tool and param are right, the allowlist is
just too narrow. Widen it in Settings, then let the app restart the server.

## Troubleshooting

| Symptom | Look at |
|---|---|
| No `aw__google_workspace__*` tools anywhere | `GET /api/apps/google-workspace-mcp/status` → `mcp_server_enabled`. False means no OAuth client saved, so the app wrote an empty `mcpServers` on purpose. |
| Tools listed, every call fails auth | `authorized_accounts` is empty — consent was never completed. Import a token or run `start_google_auth`. |
| Service not running | `GET /api/apps/google-workspace-mcp/logs`. Exit code 78 from the launcher means the venv is missing — `POST /install`. |
| Changed a setting, nothing happened | The server restarts on save; check `service.running` in `/status`, then `/logs`. |
| Gateway shows the server but calls time out | The gateway reaches it by container hostname. If the workspace container was recreated, activate rewrites `mcp.json` with the new hostname — restart the app. |
