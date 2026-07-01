const fs = require("fs");
const path = require("path");
const config = require("../lib/config");
const { sendJson } = require("../lib/util");

function listReports() {
  if (!fs.existsSync(config.REPORTS_DIR)) return [];
  return fs
    .readdirSync(config.REPORTS_DIR)
    .filter((f) => f.endsWith(".html"))
    .map((f) => {
      const stat = fs.statSync(path.join(config.REPORTS_DIR, f));
      const match = f.match(/^report_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})\.html$/);
      const range = match ? `${match[1]} ${match[2].replace("-", ":")}` : f;
      return { file: f, range, size: stat.size, generated: stat.mtime.toISOString() };
    })
    .sort((a, b) => b.generated.localeCompare(a.generated));
}

function handler(req, res) {
  sendJson(res, 200, listReports());
}

module.exports = { handler, listReports };
