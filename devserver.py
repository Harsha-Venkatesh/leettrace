#!/usr/bin/env python3
"""Dev-only server: serves web/ and runs the tracer on POST /trace (the Mac app uses a native bridge instead)."""
import http.server
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
TRACER = os.path.join(ROOT, "tracer", "tracer.py")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def do_POST(self):
        if self.path != "/trace":
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        out = subprocess.run([sys.executable, "-I", TRACER], input=body, capture_output=True, timeout=20).stdout
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
