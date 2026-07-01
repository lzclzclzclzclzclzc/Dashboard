const http = require("http");
const fs = require("fs");
const path = require("path");

// load .env before any config module reads it
(function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    const key = trimmed.slice(0, idx).trim();
    const val = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
    if (!process.env[key]) process.env[key] = val;
  }
})(path.join(__dirname, ".env"));

const config = require("./lib/config");
require("./logger");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
};

// --- route table ---
const routes = new Map();
routes.set("/api/metrics", require("./routes/metrics").handler);
routes.set("/api/system", require("./routes/system").handler);
routes.set("/api/drive", require("./routes/drive").handler);
routes.set("/api/deepseek", require("./routes/deepseek").handler);
routes.set("/api/processes", require("./routes/processes").handler);
routes.set("/api/gpu", require("./routes/gpu").handler);
routes.set("/api/reports", require("./routes/reports").handler);

// --- static file serving ---
function sendStatic(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
  const target = path.normalize(path.join(config.PUBLIC_DIR, pathname));

  if (!target.startsWith(config.PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end("Forbidden");
  }

  fs.readFile(target, (err, content) => {
    if (err) {
      res.writeHead(404);
      return res.end("Not found");
    }
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(target)] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    res.end(content);
  });
}

// --- server ---
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const handler = routes.get(url.pathname);

    if (req.method === "GET" && handler) {
      return await handler(req, res);
    }
    if (req.method === "GET") {
      return sendStatic(req, res);
    }

    const body = JSON.stringify({ error: "Method not allowed" });
    res.writeHead(405, { "Content-Type": MIME[".json"], "Content-Length": Buffer.byteLength(body) });
    res.end(body);
  } catch (err) {
    if (!res.headersSent) {
      const body = JSON.stringify({ error: err.message });
      res.writeHead(500, { "Content-Type": MIME[".json"], "Content-Length": Buffer.byteLength(body) });
      res.end(body);
    }
  }
});

server.listen(config.PORT, () => {
  console.log(`Dashboard running at http://localhost:${config.PORT}`);
});
