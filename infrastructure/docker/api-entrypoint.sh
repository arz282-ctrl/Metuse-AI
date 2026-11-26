#!/bin/sh
set -eu

PORT="${PORT:-8000}"

if [ -f /app/apps/api/main.py ]; then
  exec uv run --no-sync uvicorn apps.api.main:app --host 0.0.0.0 --port "$PORT"
fi

exec python -c '
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/healthz", "/readyz"):
            body = json.dumps({"status": "ok" if self.path == "/healthz" else "ready", "mode": "m1-placeholder"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        pass

ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
'
