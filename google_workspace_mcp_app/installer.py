"""Creates this app's private venv and installs ``workspace-mcp`` into it.

Why a private venv at all
-------------------------
``workspace-mcp`` pins its own ``starlette`` / ``cryptography`` / ``mcp`` /
``fastmcp`` / ``urllib3``. In the monolith, installing it into the shared
``.venv/aw`` upgraded those under every other Python MCP server and under
awserv itself, and had to be unpicked by re-pinning four packages by hand
(recorded in the ported skill). The workspace here has the same shape — one
shared venv at ``$AW_WORKSPACE_HOME/venv`` — so the same rule applies: this
package never touches it.

Why ``PYTHONPATH`` is scrubbed
------------------------------
This container exports ``PYTHONPATH=$AW_WORKSPACE_HOME/venv/lib/python3.12/
site-packages`` process-wide. A venv does not override ``PYTHONPATH`` — it is
searched *first*. Two consequences, both silent:

1. ``pip install`` inside the fresh venv sees fastapi / requests / urllib3
   already importable and installs **none of them**. The install exits 0 and
   the server then dies on ``ModuleNotFoundError: requests`` at first start.
2. Even a correctly-populated venv imports the shared tree's ``mcp`` package
   ahead of its own and blows up inside fastmcp.

Both were hit while porting this app (2026-08-17). Every subprocess here — and
the managed service itself, see ``scripts/run_server.sh`` — therefore runs with
``PYTHONPATH`` removed from the environment, and the venv is built by the
system interpreter rather than by whatever ``sys.executable`` happens to be.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

from . import paths

log = logging.getLogger("aw_apps.google-workspace-mcp")

SYSTEM_PYTHON = "/usr/bin/python3"
PIP_TIMEOUT_S = 900


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of this process's environment with ``PYTHONPATH`` removed."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if extra:
        env.update(extra)
    return env


def _python() -> str:
    return SYSTEM_PYTHON if os.path.exists(SYSTEM_PYTHON) else (
        shutil.which("python3") or "python3")


def is_installed(package_dir: str) -> bool:
    return paths.venv_bin(package_dir).exists()


def ensure_installed(package_dir: str, version: str) -> dict:
    """Idempotent: returns immediately when the venv already has the binary.

    Called from ``activate()`` on every boot, so an app update (which drops
    ``.data/``) self-heals without anyone noticing.
    """
    if is_installed(package_dir):
        return {"ok": True, "installed": True, "action": "noop"}
    return install(package_dir, version)


def install(package_dir: str, version: str) -> dict:
    venv = paths.venv_dir(package_dir)
    spec = f"workspace-mcp=={version}" if version else "workspace-mcp"
    env = clean_env()

    if not (venv / "bin" / "pip").exists():
        shutil.rmtree(venv, ignore_errors=True)
        rc = subprocess.run([_python(), "-m", "venv", str(venv)],
                            env=env, capture_output=True, text=True, timeout=300)
        if rc.returncode != 0:
            return {"ok": False, "installed": False,
                    "error": f"venv creation failed: {rc.stderr.strip()[-800:]}"}

    rc = subprocess.run([str(venv / "bin" / "pip"), "install", "--no-input", "-q", spec],
                        env=env, capture_output=True, text=True, timeout=PIP_TIMEOUT_S)
    if rc.returncode != 0:
        return {"ok": False, "installed": False,
                "error": f"pip install {spec} failed: {rc.stderr.strip()[-800:]}"}

    if not is_installed(package_dir):
        return {"ok": False, "installed": False,
                "error": f"pip install {spec} reported success but no workspace-mcp binary appeared"}

    log.info("google-workspace-mcp: installed %s into %s", spec, venv)
    return {"ok": True, "installed": True, "action": "installed", "spec": spec}


def installed_version(package_dir: str) -> str | None:
    """Version actually present in the venv, which is what matters — the
    configured ``pin_version`` only says what a *fresh* install would get."""
    if not is_installed(package_dir):
        return None
    pip = paths.venv_dir(package_dir) / "bin" / "pip"
    try:
        rc = subprocess.run([str(pip), "show", "workspace-mcp"],
                            env=clean_env(), capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    for line in rc.stdout.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None
