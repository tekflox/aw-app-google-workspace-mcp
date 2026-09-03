"""Where this app keeps the two things it owns on disk.

Two different lifetimes, deliberately two different roots:

``<package_dir>/.data/``  — **disposable**
    The private venv and the generated server env file. Both are derived
    artefacts: ``plugin.activate()`` rebuilds either one if it is missing, so
    losing them to an app update (which is uninstall + install, and takes the
    package dir with it) costs a reinstall of a pip package, nothing more.
    Same location and reasoning as aw-app-codegraphcontext's ``.data/venv``.

``$AW_WORKSPACE_HOME/data/google-workspace-mcp/credentials/`` — **durable**
    The Google OAuth token files (``<email>.json``). These are NOT derivable:
    losing one means a human has to sit through Google's consent screen again.
    ``AW_WORKSPACE_HOME`` (``/opt/aw-workspace/.aw-workspace``) is the
    workspace's documented home for state that must survive an app reinstall
    — see the workspace's own AGENTS.md, "Durable state".

Nothing here imports the host runtime (``src.apps.paths``): a Tier-1 app only
touches the host through its ``ctx`` facades and plain env vars, and this repo
has no ``src/`` tree of its own to import from in CI.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_ID = "google-workspace-mcp"

DEFAULT_WORKSPACE_HOME = "/opt/aw-workspace/.aw-workspace"


def workspace_home() -> Path:
    return Path(os.environ.get("AW_WORKSPACE_HOME") or DEFAULT_WORKSPACE_HOME)


def credentials_dir() -> Path:
    """Durable OAuth token store. Created 0700 — these files are bearer
    credentials for a real Google account."""
    path = workspace_home() / "data" / APP_ID / "credentials"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def data_dir(package_dir: str) -> Path:
    path = Path(package_dir) / ".data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def venv_dir(package_dir: str) -> Path:
    return data_dir(package_dir) / "venv"


def venv_bin(package_dir: str) -> Path:
    return venv_dir(package_dir) / "bin" / "workspace-mcp"


def venv_python(package_dir: str) -> Path:
    """The venv's own interpreter — used to run one-off helper scripts (e.g.
    building an OAuth authorization URL) that need the upstream package's
    modules but aren't the long-running server itself."""
    return venv_dir(package_dir) / "bin" / "python"


def env_file(package_dir: str) -> Path:
    return data_dir(package_dir) / "server.env"
