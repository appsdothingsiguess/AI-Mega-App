#!/usr/bin/env python3
"""Cross-platform startup script for Prompter web UI.

Steps:
1. cd to repo root (directory containing this script's parent)
2. Create/activate .venv; pip install if needed
3. Always rebuild web/dist (requires Node on PATH; use --skip-frontend-build to skip)
4. Warn if Ollama is not reachable (non-blocking)
5. Start uvicorn on port 8000
6. Wait until the server accepts HTTP (any /health status code)
7. Open browser at http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8000
DEFAULT_OLLAMA_BASE = "http://192.168.0.240:11434"


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


def _frontend_sources_newer_than_dist(web_dir: Path, dist_index: Path) -> bool:
    """True when src (or vite config) is newer than the last dist build."""
    if not dist_index.exists():
        return True
    dist_mtime = dist_index.stat().st_mtime
    watch_roots = [web_dir / "src", web_dir / "index.html", web_dir / "vite.config.ts", web_dir / "package.json"]
    for root in watch_roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.stat().st_mtime > dist_mtime:
                return True
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime > dist_mtime:
                return True
    return False


def _build_frontend(*, force: bool = True) -> None:
    """Build web/dist. Default force=True so start always ships current UI sources."""
    web_dir = REPO_ROOT / "web"
    dist_index = web_dir / "dist" / "index.html"

    if not web_dir.exists():
        _log("WARNING: web/ directory not found — skipping frontend build.")
        return

    if not force and dist_index.exists() and not _frontend_sources_newer_than_dist(web_dir, dist_index):
        _log("Web UI already built (web/dist up to date).")
        return

    if force:
        _log("Building web UI (npm run build)...")
    elif dist_index.exists():
        _log("Frontend sources newer than web/dist — rebuilding...")
    else:
        _log("Building web UI (npm install && npm run build)...")

    try:
        # Install only when node_modules is missing (faster restarts).
        if not (web_dir / "node_modules").is_dir():
            _log("Installing frontend dependencies (npm install)...")
            subprocess.check_call(
                ["npm", "install"], cwd=str(web_dir), shell=(sys.platform == "win32")
            )
        subprocess.check_call(
            ["npm", "run", "build"], cwd=str(web_dir), shell=(sys.platform == "win32")
        )
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


def _ollama_base_url() -> str:
    """Prefer settings.json ollama.base_url; fall back to config default."""
    settings_path = REPO_ROOT / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        base = data.get("ollama", {}).get("base_url")
        if isinstance(base, str) and base.strip():
            return base.rstrip("/")
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return DEFAULT_OLLAMA_BASE.rstrip("/")


def _check_ollama() -> None:
    """Warn (but don't block) if Ollama is not reachable."""
    base = _ollama_base_url()
    url = f"{base}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            if resp.status < 400:
                _log(f"Ollama is reachable at {base}.")
                return
    except (urllib.error.URLError, OSError, TimeoutError):
        pass
    _log(
        f"WARNING: Ollama does not appear to be reachable at {base}.  "
        "Local-model chat will fail until it is started."
    )


def _wait_for_server(health_url: str, timeout: float = 60.0) -> bool:
    """Poll until the HTTP server answers, or timeout expires.

    ``/health`` returns 503 when dependencies are degraded/down.  That still
    means uvicorn is up — do not treat non-2xx as "not ready".

    The health handler probes backends with ~3 s timeouts each, so the client
    timeout must be longer than a full report (~10 s+).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=15) as _:
                return True
        except urllib.error.HTTPError:
            # Connected and got a response (e.g. 503 degraded) — server is up.
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the Prompter web UI")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default=HOST)
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Skip npm run build (use existing web/dist as-is)",
    )
    args = parser.parse_args(argv)

    server_url = f"http://{args.host}:{args.port}"
    health_url = f"{server_url}/health"

    os.chdir(REPO_ROOT)
    _log(f"Working directory: {REPO_ROOT}")

    python = _ensure_venv()
    _install_deps(python)
    if args.skip_frontend_build:
        _log("Skipping frontend build (--skip-frontend-build).")
    else:
        _build_frontend(force=True)
    _check_ollama()

    _log(f"Starting Prompter server at {server_url} ...")
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
    ready = _wait_for_server(health_url, timeout=60.0)
    if not ready:
        _log("ERROR: Server did not respond within 60 s.  Check the output above.")
        server_proc.terminate()
        return 1

    _log(f"Server is up: {server_url}")

    if not args.no_browser:
        webbrowser.open(server_url)

    _log("Press Ctrl+C to stop.")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        _log("Shutting down...")
        server_proc.terminate()

    return 0


if __name__ == "__main__":
    sys.exit(main())
