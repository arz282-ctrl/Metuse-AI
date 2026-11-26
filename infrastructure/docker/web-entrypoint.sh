#!/bin/sh
set -eu

if [ -f /app/apps/web/package.json ]; then
  cd /app/apps/web
  if [ -d /app/apps/web/.next ]; then
    exec pnpm start -- -H 0.0.0.0 -p 3000
  fi
  exec pnpm dev -- -H 0.0.0.0 -p 3000
fi

exec node -e '
const http = require("http");
const server = http.createServer((req, res) => {
  if (req.url === "/" || req.url === "/api/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", mode: "m1-placeholder" }));
    return;
  }
  res.writeHead(404);
  res.end();
});
server.listen(3000, "0.0.0.0");
'
