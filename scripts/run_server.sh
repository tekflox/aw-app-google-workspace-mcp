#!/usr/bin/env bash
# Launcher for the managed `workspace-mcp` service.
#
# ctx.services starts a service as a plain command line with this process's
# environment and cwd = the app's package dir. Three things have to happen
# before the server binary runs, and none of them fit on a command line:
#
#   1. PYTHONPATH must go. The workspace container exports it pointing at the
#      SHARED venv's site-packages, which a virtualenv does not override — it
#      is searched first, so the private venv would import the shared tree's
#      `mcp` package and die inside fastmcp. (See installer.py for the second,
#      nastier symptom: pip silently skipping every dependency.)
#   2. The generated env file has to be sourced — it carries the OAuth client
#      secret, which must not appear in the service command line and therefore
#      in every `ps` on the host.
#   3. The bind host has to be 0.0.0.0, not the upstream default. MCP Gateway
#      reaches this server from its OWN container by our container hostname;
#      a loopback bind is invisible there.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$here/.data/server.env"
bin="$here/.data/venv/bin/workspace-mcp"

unset PYTHONPATH

if [[ -f "$env_file" ]]; then
  # shellcheck disable=SC1090
  source "$env_file"
fi

if [[ ! -x "$bin" ]]; then
  echo "run_server.sh: $bin is missing — the app's venv was not installed." >&2
  echo "Restart the app, or POST /api/apps/google-workspace-mcp/install." >&2
  exit 78  # EX_CONFIG: a setup problem, not a crash
fi

# resolve_bind_host_for_transport() in the server's main.py defaults
# streamable-http to 127.0.0.1 unless WORKSPACE_MCP_HOST is set explicitly —
# it warns loudly about it, on the assumption that "reachable" means "on the
# internet". Here it means "on the container network, where MCP Gateway is",
# and nothing publishes this port to the host.
export WORKSPACE_MCP_HOST="${WORKSPACE_MCP_HOST:-0.0.0.0}"

# workspace-mcp's own main.py renders every startup diagnostic — every
# validation-failure exit, and the final HTTP-bind error — through a
# safe_print() that checks sys.stderr.isatty(). Under ServiceSupervisor's
# piped, non-TTY subprocess that check is always false, so safe_print()
# downgrades the message to logger.debug() instead of printing it — and
# logging.basicConfig(level=INFO) (also upstream) drops a .debug() call
# before it reaches any handler. Net effect: any startup failure produces
# zero diagnostics anywhere, ever. We can't patch the installed package, so
# allocate a real pty for it instead — `script` makes isatty() true and the
# real error text gets printed and captured by ServiceSupervisor like any
# other log line. Scoped to this app's own launcher, not the shared
# ServiceSupervisor every other managed service goes through.
if command -v script >/dev/null 2>&1; then
  quoted="$bin"
  for arg in "$@"; do
    quoted+=" $(printf '%q' "$arg")"
  done
  exec script -qec "$quoted" /dev/null
fi

exec "$bin" "$@"
