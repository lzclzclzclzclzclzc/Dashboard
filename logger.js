const Database = require("better-sqlite3");
const path = require("path");
const http = require("http");

const DB_PATH = path.join(__dirname, "data", "telemetry.db");
const POLL_MS = 60_000;
const RETENTION_DAYS = 30;
const BASE_URL = `http://localhost:${process.env.PORT || 3000}`;

let db;
let timer;

function init() {
  db = new Database(DB_PATH);
  db.pragma("journal_mode = WAL");
  db.pragma("synchronous = NORMAL");
  db.pragma("busy_timeout = 5000");

  db.exec(`
    CREATE TABLE IF NOT EXISTS telemetry (
      ts                  TEXT PRIMARY KEY,
      cpu_pct             REAL,
      cpu_temp            REAL,
      cpu_power           REAL,
      mem_pct             REAL,
      mem_used_gb         REAL,
      mem_total_gb        REAL,
      disk_free_gb        REAL,
      disk_total_gb       REAL,
      disk_pct            REAL,
      cpu_top10           TEXT,
      mem_top10           TEXT,
      ds_balance          TEXT,
      ds_daily_used       TEXT,
      gpu_util            REAL,
      gpu_mem_dedicated_mb REAL,
      gpu_mem_shared_mb   REAL,
      gpu_mem_total_mb    REAL,
      gpu_power_w         REAL,
      gpu_temp            REAL
    );
    CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts);
  `);

  // Add GPU columns to existing databases
  const gpuColumns = [
    'gpu_util REAL',
    'gpu_mem_dedicated_mb REAL',
    'gpu_mem_shared_mb REAL',
    'gpu_mem_total_mb REAL',
    'gpu_power_w REAL',
    'gpu_temp REAL'
  ];
  for (const col of gpuColumns) {
    try { db.exec(`ALTER TABLE telemetry ADD COLUMN ${col}`); } catch { /* already exists */ }
  }

  console.log(`[logger] SQLite ready at ${DB_PATH}`);
  schedule();
}

function schedule() {
  timer = setInterval(pollAndInsert, POLL_MS);
  pollAndInsert();
}

async function pollAndInsert() {
  try {
    const [system, drive, deepseek, processes, gpu] = await Promise.all([
      fetchJson("/api/system"),
      fetchJson("/api/drive"),
      fetchJson("/api/deepseek"),
      fetchJson("/api/processes"),
      fetchJson("/api/gpu")
    ]);

    const ts = localTimestamp();
    const memUsed = (system.memory?.used || 0) / (1024 ** 3);
    const memTotal = (system.memory?.total || 0) / (1024 ** 3);

    const stmt = db.prepare(`
      INSERT OR REPLACE INTO telemetry
        (ts, cpu_pct, cpu_temp, cpu_power,
         mem_pct, mem_used_gb, mem_total_gb,
         disk_free_gb, disk_total_gb, disk_pct,
         cpu_top10, mem_top10,
         ds_balance, ds_daily_used,
         gpu_util, gpu_mem_dedicated_mb, gpu_mem_shared_mb, gpu_mem_total_mb, gpu_power_w, gpu_temp)
      VALUES
        (@ts, @cpu_pct, @cpu_temp, @cpu_power,
         @mem_pct, @mem_used_gb, @mem_total_gb,
         @disk_free_gb, @disk_total_gb, @disk_pct,
         @cpu_top10, @mem_top10,
         @ds_balance, @ds_daily_used,
         @gpu_util, @gpu_mem_dedicated_mb, @gpu_mem_shared_mb, @gpu_mem_total_mb, @gpu_power_w, @gpu_temp)
    `);

    stmt.run({
      ts,
      cpu_pct: system.cpu?.percent ?? null,
      cpu_temp: system.cpu?.temp_celsius ?? null,
      cpu_power: system.cpu?.power_watts ?? null,
      mem_pct: system.memory?.percent ?? null,
      mem_used_gb: round2(memUsed),
      mem_total_gb: round2(memTotal),
      disk_free_gb: round2((drive.drive?.free || 0) / (1024 ** 3)),
      disk_total_gb: round2((drive.drive?.total || 0) / (1024 ** 3)),
      disk_pct: drive.drive?.free_percent ?? null,
      cpu_top10: safeJson(processes.cpu),
      mem_top10: safeJson(processes.memory),
      ds_balance: deepseek.balance?.ok
        ? safeJson(deepseek.balance.data?.balance_infos)
        : null,
      ds_daily_used: safeJson(deepseek.daily?.items),
      gpu_util: gpu?.utilization_gpu ?? null,
      gpu_mem_dedicated_mb: gpu?.memory?.dedicated_used_mb ?? null,
      gpu_mem_shared_mb: gpu?.memory?.shared_used_mb ?? null,
      gpu_mem_total_mb: gpu?.memory?.dedicated_total_mb ?? null,
      gpu_power_w: gpu?.power?.draw_w ?? null,
      gpu_temp: gpu?.temperature ?? null
    });

    // expire old rows
    db.prepare(
      `DELETE FROM telemetry WHERE ts < datetime('now', '-' || @days || ' days', 'localtime')`
    ).run({ days: RETENTION_DAYS });

  } catch (err) {
    console.error(`[logger] poll error: ${err.message}`);
  }
}

function fetchJson(pathname) {
  return new Promise((resolve, reject) => {
    http.get(`${BASE_URL}${pathname}`, { headers: { "Cache-Control": "no-store" } }, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (e) {
          reject(new Error(`Invalid JSON from ${pathname}: ${e.message}`));
        }
      });
    }).on("error", reject);
  });
}

function localTimestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function safeJson(value) {
  if (value === null || value === undefined) return null;
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

function stop() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (db) {
    db.close();
    db = null;
    console.log("[logger] DB closed");
  }
}

process.on("exit", stop);
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("uncaughtException", (err) => {
  console.error(`[logger] uncaught: ${err.message}`);
  stop();
});

init();
