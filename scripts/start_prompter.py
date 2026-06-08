#!/usr/bin/env python3
"""Cross-platform startup script for Prompter web UI.

Steps:
1. cd to repo root (directory containing this script's parent)
2. Create/activate .venv; pip install if needed
3. Build web/dist if missing (requires Node on PATH)
4. Warn if LM Studio is not running (non-blocking)
5. Start uvicorn on port 8000
6. Wait for /health to respond
7. Open browser at http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import webbrowser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8000
SERVER_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{SERVER_URL}/health"
LM_HEALTH_URL = "http://127.0.0.1:1234/api/v1/models"


def _log(msg: str) -> None:
    print(f"[Prompter] {msg}", flush=True)


def _ensure_venv() -> Path:
    """Return path to python inside .venv, creating it if needed."""
    venv = REPO_ROOT / ".venv"
    if sys.platform == "win32":
        python = venv / "Scripts" / "python.exe"
    else:
        python = venv / "bin" / "python"

    if not python.exists():
        _log("Creating virtual environment (.venv)...")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])

    return python


def _install_deps(python: Path) -> None:
    """Install the package if not already installed."""
    try:
        subprocess.check_call(
            [str(python), "-c", "import app.main"],
            cwd=str(REPO_ROOT),
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        _log("Python dependencies already installed.")
    except subprocess.CalledProcessError:
        _log("Installing Python dependencies (pip install -e .[dev])...")
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "-q", "-e", ".[dev]"],
            cwd=str(REPO_ROOT),
        )


def _build_frontend() -> None:
    """Build web/dist if index.html is missing."""
    dist_index = REPO_ROOT / "web" / "dist" / "index.html"
    if dist_index.exists():
        _log("Web UI already built (web/dist/index.html found).")
        return

    web_dir = REPO_ROOT / "web"
    if not web_dir.exists():
        _log("WARNING: web/ directory not found — skipping frontend build.")
        return

    _log("Building web UI (npm install && npm run build)...")
    try:
        subprocess.check_call(["npm", "install"], cwd=str(web_dir), shell=(sys.platform == "win32"))
        subprocess.check_call(["npm", "run", "build"], cwd=str(web_dir), shell=(sys.platform == "win32"))
        _log("Web UI built successfully.")
    except FileNotFoundError:
        _log(
            "ERROR: 'npm' not found on PATH.  Install Node.js from https://nodejs.org "
            "then re-run this script."
        )
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        _log(f"ERROR: npm build failed (exit {exc.returncode}).  Check output above.")
        sys.exit(1)


def _check_lmstudio() -> None:
    """Warn (but don't block) if LM Studio is not running."""
    try:
        with urllib.request.urlopen(LM_HEALTH_URL, timeout=3) as resp:
            if resp.status < 400:
                _log("LM Studio is running.")
                return
    except (urllib.error.URLError, OSError):
        pass
    _log(
        "WARNING: LM Studio does not appear to be running at localhost:1234.  "
        "Chat will fail until it is started."
    )


def _wait_for_server(timeout: float = 30.0) -> bool:
    """Poll /health until it responds or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as _:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the Prompter web UI")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default=HOST)
    args = parser.parse_args(argv)

    os.chdir(REPO_ROOT)
    _log(f"Working directory: {REPO_ROOT}")

    python = _ensure_venv()
    _install_deps(python)
    _build_frontend()
    _check_lmstudio()

    _log(f"Starting Prompter server at http://{args.host}:{args.port} ...")
    server_proc = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ],
        cwd=str(REPO_ROOT),
    )

    _log("Waiting for server to be ready...")
    ready = _wait_for_server(timeout=30.0)
    if not ready:
        _log("ERROR: Server did not respond within 30 s.  Check the output above.")
        server_proc.terminate()
        return 1

    _log(f"Server is up: {SERVER_URL}")

    if not args.no_browser:
        webbrowser.open("http://localhost:5173/")

    _log("Press Ctrl+C to stop.")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        _log("Shutting down...")
        server_proc.terminate()

    return 0


if __name__ == "__main__":
    sys.exit(main())
