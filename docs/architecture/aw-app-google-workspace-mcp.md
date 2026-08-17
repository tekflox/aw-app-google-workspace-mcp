# aw-app-google-workspace-mcp — architecture

Ported from agentic-workspace's `aw-google-workspace` entry in
`src/config/mcp.json`, 2026-08-17. The tool surface is identical (86 tools,
same seven groups); the plumbing underneath is not, and the differences are all
forced rather than stylistic.

## The shape

```
aw-mcp-gateway  ──http──▶  workspace-mcp          (managed service, workspace container)
   (container)             :8010/mcp
                           :8010/oauth2callback   ◀── Google consent redirect
                                │
                                ├── $AW_WORKSPACE_HOME/data/google-workspace-mcp/credentials/<email>.json
                                └── <app dir>/.data/venv        (private, disposable)
```

The app itself is Tier-1 (in-process). It owns four things: the private venv,
the generated `server.env`, the managed service, and `mcp.json`.

## Why not stdio, like the monolith

The monolith ran `workspace-mcp` as a stdio child of its MCP gateway, the same
way aw-app-notion runs `@notionhq/notion-mcp-server` and
aw-app-codegraphcontext runs `cgc mcp start`. That does not survive the port,
for two independent reasons — either one alone would be fatal:

1. **aw-mcp-gateway mounts `$AW_APPS_ROOT` read-only** (`"mode": "ro"` in its
   manifest). `workspace-mcp` needs a writable credentials directory, and the
   only writable path inside that container is the gateway's own app-data
   mount, which is not ours to scribble in.
2. **The OAuth callback is served by the MCP process itself.** As a stdio child
   it would bind a port inside the *gateway's* container — unreachable by a
   browser, by this app, and by anything else that could complete a consent
   flow. The monolith got away with this because its sandbox shared a netns
   with the host; nothing here does.

Running it as this app's own managed service (`ctx.services`, capability
`service:manage`) in the workspace container fixes both, and the gateway
reaches it over the container network by hostname — the same mechanism
aw-app-notion's in-process `aw-kanban` server uses.

`socket.gethostname()` is the right host to publish: it is exactly the alias
ContainerSupervisor injects into sibling containers as `AW_WORKSPACE_HOST`.
It changes when the workspace container is recreated, which is why `activate()`
rewrites `mcp.json` every boot rather than persisting it.

## The two `PYTHONPATH` traps

This container exports `PYTHONPATH` pointing at the **shared** venv's
site-packages, process-wide. A virtualenv does not override `PYTHONPATH` — it
is searched *first*. Both consequences are silent, and both were hit while
building this app:

1. `pip install workspace-mcp` inside a fresh venv sees `fastapi`, `requests`
   and `urllib3` as already importable and installs **none of them**. The
   install exits 0. The server then dies at first start on
   `ModuleNotFoundError: requests`.
2. Even a correctly-populated venv imports the shared tree's `mcp` package
   ahead of its own and blows up inside `fastmcp` with
   `cannot import name 'request_ctx'`.

So: `installer.py` builds the venv with the *system* interpreter
(`/usr/bin/python3`, not `sys.executable`) and runs every subprocess with
`PYTHONPATH` stripped; `scripts/run_server.sh` unsets it before exec. Anything
run by hand needs `env -u PYTHONPATH`.

This is also why the venv is private in the first place. `workspace-mcp` pins
its own `starlette` / `cryptography` / `urllib3` / `mcp` / `fastmcp`. In the
monolith, installing it into the shared venv upgraded those under every other
Python MCP server and under the server process itself, and had to be unpicked
by re-pinning four packages by hand.

## Two lifetimes on disk

| Path | Lifetime | Why |
|---|---|---|
| `<app dir>/.data/venv`, `.data/server.env` | disposable | Derived. An app update is uninstall + install and takes the package dir with it; `activate()` rebuilds both. |
| `$AW_WORKSPACE_HOME/data/google-workspace-mcp/credentials/` | durable | Not derivable. Losing a token means a human sits through Google's consent screen again. 0700; the files are bearer credentials for a real Google account. |

## Settings, and which path persists them

`ctx.config` handed to a plugin is a **copy** — writing to it from an app route
changes nothing that survives the request. Only core's
`POST /api/apps/google-workspace-mcp/config` persists app config, and it calls
the plugin's `on_config_saved` hook afterwards.

The OAuth client **secret** cannot use that path: it would land in plain,
cloud-syncable app config. It goes to `ctx.secrets` through this app's own
`POST /settings`, which then forwards the client *id* to core's config route
over an authenticated loopback call (`AW_WORKSPACE_API_KEY`) so the settings
form can ask for both halves of an OAuth client in one place. Asking a user to
save one half here and its twin somewhere else is how a half-configured client
happens.

Every setting feeds either the env file or the service argv, and the process
reads its environment at start — so every save restarts the server. There is no
cheap subset to skip.

## Why `mcp.json` is empty until configured

An advertised-but-unconfigured server gives every agent 86 tools that all fail
identically at call time, with an error that reads like a Google problem. An
absent server fails in Settings and in `aw-workspace-cli doctor`, where someone
is looking. Same call aw-app-notion makes with its token.

Note that `contributes.mcp.provides` in `aw-app.json` registers **nothing** —
it is the marketplace's "what you get" list. The app writes `mcp.json` itself,
and the write is skipped when the content is unchanged: the gateway reloads on
mtime, and every reload briefly drops every tool it proxies.

## Known limits

- **Completing a fresh OAuth consent needs a reachable callback.** The ported
  client is a Google *Desktop app* client, which only accepts `localhost`
  redirects — fine for the server itself, awkward for a browser on someone
  else's machine. Importing an existing token (`POST /credentials`) sidesteps
  this entirely and is the recommended path for the migration. Switching to a
  Web-app client with a public callback is possible via `oauth_redirect_uri`,
  but that URL must be registered in Google Cloud first.
- **No MCP-level auth on the HTTP transport.** The server is bound to
  `0.0.0.0` on the container network so the gateway can reach it; nothing
  publishes the port to the host. Upstream's `MCP_ENABLE_OAUTH21=true` mode
  exists for genuinely remote deployments and is not wired up here.
