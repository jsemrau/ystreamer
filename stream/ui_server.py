#!/usr/bin/env python3
"""
Serve the stream UI (stream/ui/) and the engine's runtime/ui.json.

    python -m stream.ui_server              # http://127.0.0.1:8765
    YS_UI_PORT=9000 python -m stream.ui_server

The page polls ui.json twice a second. Open it in a browser to preview the
stream; stream/stream.sh (YS_STREAM_UI=browser) captures it for YouTube.
"""

from __future__ import annotations

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
        if path == "/ui.json":
            self._send_file(os.path.join(config.RUNTIME_DIR, "ui.json"), "application/json",
                            missing=b'{"events": [], "code": [], "thinking": {}}')
            return
        super().do_GET()

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
