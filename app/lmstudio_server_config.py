"""Read/write LM Studio HTTP server config (network bind, port).

LM Studio persists server settings in ``http-server-config.json`` under
``~/.lmstudio/.internal/`` (or ``~/.cache/lm-studio/.internal/``). This is not
a documented stable API; we treat it as best-effort and tell users to restart
the LM Studio server after changes.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

_DEFAULT_PORT = 1234


def config_search_paths() -> list[Path]:
    home = Path.home()
    return [
        home / ".lmstudio" / ".internal" / "http-server-config.json",
        home / ".cache" / "lm-studio" / ".internal" / "http-server-config.json",
    ]


def find_config_path() -> Path | None:
    for path in config_search_paths():
        if path.exists():
            return path
    return None


def read_config() -> tuple[dict[str, Any], Path | None]:
    """Return (config dict, path). Empty dict if file missing."""
    path = find_config_path()
    if path is None:
        return {"port": _DEFAULT_PORT, "networkInterface": "127.0.0.1"}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("port", _DEFAULT_PORT)
    data.setdefault("networkInterface", "127.0.0.1")
    return data, path


def serve_on_local_network(config: dict[str, Any]) -> bool:
    iface = str(config.get("networkInterface", "127.0.0.1"))
    return iface not in ("127.0.0.1", "localhost", "::1")


def local_lan_ips() -> list[str]:
    """Best-effort list of LAN IPv4 addresses for this machine."""
    ips: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ips.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith("127."):
                ips.add(addr)
    except OSError:
        pass
    return sorted(ips)


def build_access_urls(port: int, *, on_network: bool) -> list[str]:
    urls = [f"http://127.0.0.1:{port}"]
    if on_network:
        for ip in local_lan_ips():
            urls.append(f"http://{ip}:{port}")
    return urls


def write_serve_on_local_network(enabled: bool) -> tuple[dict[str, Any], Path, str]:
    """Update networkInterface in config. Creates file in primary location if missing."""
    config, path = read_config()
    iface = "0.0.0.0" if enabled else "127.0.0.1"
    config["networkInterface"] = iface

    if path is None:
        path = config_search_paths()[0]
        path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    note = (
        "Restart the LM Studio server (Developer → Server → Stop, then Start) "
        "for network binding to take effect."
    )
    return config, path, note
