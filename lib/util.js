const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
};

function sendJson(response, status, data) {
  const body = JSON.stringify(data);
  response.writeHead(status, {
    "Content-Type": MIME[".json"],
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
  });
  response.end(body);
}

function clampPercent(value) {
  return Math.max(0, Math.min(100, Number(value.toFixed(1))));
}

function getDriveShape(label, total, used, free) {
  return {
    label,
    total,
    used,
    free,
    used_percent: total ? clampPercent((used / total) * 100) : 0,
    free_percent: total ? clampPercent((free / total) * 100) : 0,
  };
}

function localDate() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function roundMoney(value) {
  return Number(value.toFixed(4));
}

function maskKey(key) {
  if (!key) return "not configured";
  return `${key.slice(0, 5)}...${key.slice(-4)}`;
}

module.exports = { sendJson, clampPercent, getDriveShape, localDate, roundMoney, maskKey };
