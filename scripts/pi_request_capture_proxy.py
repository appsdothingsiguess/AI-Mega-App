#!/usr/bin/env python3
"""Transparent Pi -> llama-swap capture relay.

The relay forwards OpenAI chat-completions traffic while retaining request and
response bodies for throughput diagnosis. Captures may contain prompt content.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LISTEN_HOST = os.environ.get("PI_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("PI_PROXY_PORT", "8081"))
UPSTREAM_HOST = os.environ.get("PI_PROXY_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("PI_PROXY_UPSTREAM_PORT", "8080"))
CAPTURE_DIR = Path(os.environ.get("PI_PROXY_CAPTURE_DIR", "/tmp/pi-request-captures"))
ALLOWED_CLIENT = os.environ.get("PI_PROXY_ALLOWED_CLIENT", "192.168.0.246")


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        """Forward read-only OpenAI discovery requests to llama-swap.

        DeepSeek Harness uses GET /v1/models when a custom provider's model
        catalog is fetched. The relay is primarily a POST capture proxy, but
        discovery must pass through so clients can inspect the same model list
        exposed by the upstream endpoint.
        """
        self.close_connection = True
        if ALLOWED_CLIENT and self.client_address[0] != ALLOWED_CLIENT:
            self.send_error(403, "client not allowed")
            return
        if self.path != "/v1/models":
            self.send_error(404, "only /v1/models is available through this relay")
            return

        connection = http.client.HTTPConnection(
            UPSTREAM_HOST, UPSTREAM_PORT, timeout=30
        )
        try:
            connection.request("GET", self.path, headers={"Host": f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"})
            response = connection.getresponse()
            body = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {"content-length", "transfer-encoding", "connection"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        finally:
            connection.close()

    def do_POST(self) -> None:  # noqa: N802
        self.close_connection = True
        if ALLOWED_CLIENT and self.client_address[0] != ALLOWED_CLIENT:
            self.send_error(403, "client not allowed")
            return

        length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(length)
        connection = http.client.HTTPConnection(
            UPSTREAM_HOST, UPSTREAM_PORT, timeout=300
        )
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        try:
            connection.request("POST", self.path, body=request_body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {"content-length", "transfer-encoding", "connection"}:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            response_body = bytearray()
            while True:
                chunk = response.read1(8192)
                if not chunk:
                    break
                response_body.extend(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()

        capture = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "client": self.client_address[0],
            "path": self.path,
            "request": self._decode_json(request_body),
            "response_status": response.status,
            "response_headers": dict(response.getheaders()),
            "response_body": response_body.decode("utf-8", errors="replace"),
        }
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (CAPTURE_DIR / f"{stamp}.json").write_text(
            json.dumps(capture, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _decode_json(body: bytes) -> object:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body.decode("utf-8", errors="replace")

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {format % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(
        f"Pi capture relay listening on {LISTEN_HOST}:{LISTEN_PORT}; "
        f"upstream {UPSTREAM_HOST}:{UPSTREAM_PORT}; "
        f"allowed client {ALLOWED_CLIENT or 'any'}; captures {CAPTURE_DIR}",
        flush=True,
    )
    server.serve_forever()
