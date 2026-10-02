#!/usr/bin/env python3
"""
Serve the stream UI (stream/ui/) and the engine's runtime/ui.json.

    python -m stream.ui_server              # http://127.0.0.1:8765
    YS_UI_PORT=9000 python -m stream.ui_server

The page polls ui.json twice a second. Open it in a browser to preview the
stream; stream/stream.sh (YS_STREAM_UI=browser) captures it for YouTube.
"""

from __future__ import annotations

import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:   # quiet: the page polls twice a second
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            # The page the stream shows (stream.sh captures the same one).
            self.path = "/" + os.getenv("YS_UI_PAGE", "deck.html") + self.path[1:]
            super().do_GET()
            return
        if path == "/ui.json":
            self._send_file(os.path.join(config.RUNTIME_DIR, "ui.json"), "application/json",
                            missing=b'{"events": [], "code": [], "thinking": {}}')
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        """A request from the host ("more bass"), typed into the page. The
        server only listens on 127.0.0.1, so only this machine can send one."""
        path = self.path.split("?", 1)[0]
        if path not in ("/request", "/rate"):
            self.send_error(404)
            return
        from music import host_requests

        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            data = json.loads(self.rfile.read(length) or b"{}")
            if path == "/rate":     # 👍 / 👎 on what is playing, or on one line of it
                record = host_requests.submit_rating(data.get("rating"), data.get("role"))
            else:
                record = host_requests.submit(data.get("text", ""))
        except (ValueError, OSError):
            record = None
        body = json.dumps({"ok": bool(record), "text": record.get("text", "") if record else ""}).encode()
        self.send_response(200 if record else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str, missing: bytes) -> None:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            data = missing
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    port = int(os.getenv("YS_UI_PORT", "8765"))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=UI_DIR))
    except OSError as e:
        sys.exit(f"[ui] can't serve on port {port} ({e.strerror}); is another stream page already running? "
                 "Set YS_UI_PORT to use another port")
    print(f"[ui] serving the stream UI on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
