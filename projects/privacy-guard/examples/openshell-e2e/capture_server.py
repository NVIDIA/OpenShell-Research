"""Capture one HTTP POST body for the Privacy Guard OpenShell E2E example."""

from __future__ import annotations

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_CAPTURE_BYTES = 1024 * 1024


class _CaptureHandler(BaseHTTPRequestHandler):
    output_path: Path

    def do_POST(self) -> None:
        """Persist one bounded request body and return a deterministic response."""
        content_length = int(self.headers.get("content-length", "0"))
        if content_length < 0 or content_length > MAX_CAPTURE_BYTES:
            self.send_error(413)
            return

        request_body = self.rfile.read(content_length)
        temporary_path = self.output_path.with_suffix(".tmp")
        temporary_path.write_bytes(request_body)
        os.replace(temporary_path, self.output_path)

        response_body = b'{"captured":true}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

        # The example expects exactly one request.
        self.server.shutdown()

    def log_message(self, format: str, *args: object) -> None:
        """Suppress request content and default access-log output."""


def main() -> int:
    """Serve until one request is captured."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    _CaptureHandler.output_path = arguments.output
    server = ThreadingHTTPServer((arguments.host, arguments.port), _CaptureHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
