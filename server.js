const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");
const { promisify } = require("util");

const execFileAsync = promisify(execFile);
const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, "public");
const DATA_DIR = path.join(ROOT, "data");
const BASELINE_FILE = path.join(DATA_DIR, "deepseek-baseline.json");

require("./logger");

loadEnv(path.join(ROOT, ".env"));

const REPORTS_DIR = path.join(PUBLIC_DIR, "reports");
const PORT = Number(process.env.PORT || 3000);
const DEEPSEEK_API_KEY = process.env.DEEPSEEK_API_KEY || "";
const LHM_DATA_URL = process.env.LIBRE_HARDWARE_MONITOR_URL || "http://192.168.18.154:8085/data.json";
const CPU_POWER_SENSOR_ID = process.env.CPU_POWER_SENSOR_ID || "/intelcpu/0/power/0";
const CPU_TEMP_SENSOR_ID = process.env.CPU_TEMP_SENSOR_ID || "/intelcpu/0/temperature/18";
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8"
};

let previousCpu = readCpuSnapshot();
let cpuPowerCache = { at: 0, value: null, retryAfter: 0 };
let cpuTempCache = { at: 0, value: null, retryAfter: 0 };

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (!process.env[key]) process.env[key] = value;
  }
}

function readCpuSnapshot() {
  const cpus = os.cpus();
  const totals = cpus.map((cpu) => {
    const idle = cpu.times.idle;
    const total = Object.values(cpu.times).reduce((sum, time) => sum + time, 0);
    return { idle, total };
  });
  return { cpus: totals, at: Date.now() };
}

function getCpuUsage() {
  const current = readCpuSnapshot();
  const values = current.cpus.map((cpu, index) => {
    const previous = previousCpu.cpus[index] || cpu;
    const idleDelta = cpu.idle - previous.idle;
    const totalDelta = cpu.total - previous.total;
    return totalDelta > 0 ? 1 - idleDelta / totalDelta : 0;
  });
  previousCpu = current;
  const average = values.reduce((sum, value) => sum + value, 0) / Math.max(values.length, 1);
  return {
    percent: clampPercent(average * 100),
    cores: values.map((value) => clampPercent(value * 100)),
    model: os.cpus()[0]?.model || "Unknown CPU"
  };
}

function getMemoryUsage() {
  const total = os.totalmem();
  const free = os.freemem();
  const used = total - free;
  return {
    total,
    used,
    free,
    percent: clampPercent((used / total) * 100)
  };
}

async function getDriveUsage() {
  if (process.platform === "win32") {
    const { stdout } = await execFileAsync("powershell", [
      "-NoProfile",
      "-Command",
      "$d=[System.IO.DriveInfo]::new('C'); [pscustomobject]@{Size=$d.TotalSize;FreeSpace=$d.AvailableFreeSpace} | ConvertTo-Json -Compress"
    ]);
    const disk = JSON.parse(stdout.trim() || "{}");
    const total = Number(disk.Size || 0);
    const free = Number(disk.FreeSpace || 0);
    const used = Math.max(total - free, 0);
    return getDriveShape("C:", total, used, free);
  }

  const { stdout } = await execFileAsync("df", ["-k", "/"]);
  const [, line] = stdout.trim().split(/\r?\n/);
  const parts = line.trim().split(/\s+/);
  const total = Number(parts[1]) * 1024;
  const used = Number(parts[2]) * 1024;
  const free = Number(parts[3]) * 1024;
  return getDriveShape("/", total, used, free);
}

async function getDeepSeekBalance() {
  if (!DEEPSEEK_API_KEY) {
    return { ok: false, status: 0, message: "DEEPSEEK_API_KEY is not configured.", data: null };
  }

  try {
    const response = await fetch("https://api.deepseek.com/user/balance", {
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${DEEPSEEK_API_KEY}`
      }
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    return {
      ok: response.ok,
      status: response.status,
      message: response.ok ? "ok" : `DeepSeek returned ${response.status}`,
      data
    };
  } catch (error) {
    return { ok: false, status: 0, message: error.message, data: null };
  }
}

async function getMetrics() {
  const [drive, deepseek] = await Promise.all([getDriveUsage(), getDeepSeekSnapshot()]);
  const cpu = getCpuUsage();
  cpu.power_watts = await getCpuPowerWatts();
  return {
    at: new Date().toISOString(),
    host: {
      name: os.hostname(),
      platform: `${os.type()} ${os.release()}`,
      uptime: os.uptime()
    },
    cpu,
    memory: getMemoryUsage(),
    drive,
    deepseek
  };
}

async function getSystemMetrics() {
  const cpu = getCpuUsage();
  const [power, temp] = await Promise.all([getCpuPowerWatts(), getCpuTempCelsius()]);
  cpu.power_watts = power;
  cpu.temp_celsius = temp;
  return {
    at: new Date().toISOString(),
    host: {
      name: os.hostname(),
      platform: `${os.type()} ${os.release()}`,
      uptime: os.uptime()
    },
    cpu,
    memory: getMemoryUsage()
  };
}

async function getDriveSnapshot() {
  return {
    at: new Date().toISOString(),
    drive: await getDriveUsage()
  };
}

async function getDeepSeekSnapshot() {
  const balance = await getDeepSeekBalance();
  return {
    at: new Date().toISOString(),
    key_mask: maskKey(DEEPSEEK_API_KEY),
    balance,
    daily: getDeepSeekDaily(balance)
  };
}

async function getTopProcesses() {
  if (process.platform !== "win32") {
    return { cpu: [], memory: [], error: "Only supported on Windows" };
  }
  try {
    const cpuCmd = `Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 | ForEach-Object { [pscustomobject]@{Name=$_.ProcessName;Id=$_.Id;CPU=$([math]::Round($_.CPU,1));MemMB=$([math]::Round($_.WorkingSet64/1MB,1))} } | ConvertTo-Json -Compress`;
    const memCmd = `Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 | ForEach-Object { [pscustomobject]@{Name=$_.ProcessName;Id=$_.Id;CPU=$([math]::Round($_.CPU,1));MemMB=$([math]::Round($_.WorkingSet64/1MB,1))} } | ConvertTo-Json -Compress`;

    const [cpuResult, memResult] = await Promise.all([
      execFileAsync("powershell", ["-NoProfile", "-Command", cpuCmd]),
      execFileAsync("powershell", ["-NoProfile", "-Command", memCmd])
    ]);

    const cpuList = JSON.parse(cpuResult.stdout.trim() || "[]");
    const memList = JSON.parse(memResult.stdout.trim() || "[]");
    return {
      cpu: Array.isArray(cpuList) ? cpuList : [],
      memory: Array.isArray(memList) ? memList : []
    };
  } catch (error) {
    return { cpu: [], memory: [], error: error.message };
  }
}

// --- GPU metrics via nvidia-smi + WDDM perf counters ---

let gpuMetricsCache = { at: 0, value: null, retryAfter: 0 };
let gpuInstanceName = null;

async function discoverGpuInstance() {
  if (gpuInstanceName) return gpuInstanceName;
  try {
    const { stdout } = await execFileAsync("powershell", [
      "-NoProfile", "-Command",
      "(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples | Where-Object { $_.CookedValue -gt 0 } | ForEach-Object { $_.InstanceName } | Select-Object -First 1"
    ]);
    gpuInstanceName = stdout.trim();
    if (gpuInstanceName) console.log(`[gpu] discovered GPU perf instance: ${gpuInstanceName}`);
  } catch (err) {
    console.error(`[gpu] failed to discover GPU perf instance: ${err.message}`);
  }
  return gpuInstanceName;
}

async function getGpuMetrics() {
  const now = Date.now();
  if (now < gpuMetricsCache.retryAfter) return gpuMetricsCache.value;
  if (now - gpuMetricsCache.at < 900) return gpuMetricsCache.value;

  try {
    const [smiResult] = await Promise.all([
      execFileAsync("nvidia-smi", [
        "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,temperature.gpu",
        "--format=csv,noheader,nounits"
      ]),
      discoverGpuInstance()
    ]);

    const raw = smiResult.stdout.trim();
    if (!raw) throw new Error("nvidia-smi returned empty output");

    const parts = raw.split(/,\s*/);
    if (parts.length < 8) throw new Error(`nvidia-smi unexpected output: ${raw}`);

    const name = parts[0];
    const utilGpu = parseFloat(parts[1]) || 0;
    const utilMem = parseFloat(parts[2]) || 0;
    const memUsed = parseFloat(parts[3]) || 0;
    const memTotal = parseFloat(parts[4]) || 0;
    const powerDraw = parseFloat(parts[5]) || 0;
    const powerLimit = parseFloat(parts[6]) || 0;
    const tempGpu = parseFloat(parts[7]) || 0;

    let sharedMemMb = null;
    const instance = gpuInstanceName;
    if (instance) {
      try {
        const { stdout: sharedOut } = await execFileAsync("powershell", [
          "-NoProfile", "-Command",
          `(Get-Counter "\\GPU Adapter Memory(${instance})\\Shared Usage" -ErrorAction SilentlyContinue).CounterSamples | Select-Object -ExpandProperty CookedValue`
        ]);
        const rawShared = parseFloat(sharedOut.trim());
        if (Number.isFinite(rawShared)) {
          sharedMemMb = Math.round((rawShared / (1024 * 1024)) * 10) / 10;
        }
      } catch { /* shared memory unavailable */ }
    }

    const result = {
      at: new Date().toISOString(),
      name,
      utilization_gpu: utilGpu,
      utilization_memory: utilMem,
      memory: {
        dedicated_used_mb: memUsed,
        dedicated_total_mb: memTotal,
        shared_used_mb: sharedMemMb
      },
      power: {
        draw_w: powerDraw,
        limit_w: powerLimit
      },
      temperature: tempGpu
    };

    gpuMetricsCache = { at: now, value: result, retryAfter: now };
    return result;
  } catch (err) {
    console.error(`[gpu] metrics error: ${err.message}`);
    gpuMetricsCache = { at: now, value: null, retryAfter: now + 5000 };
    return null;
  }
}

async function getCpuTempCelsius() {
  const now = Date.now();
  if (now < cpuTempCache.retryAfter) return cpuTempCache.value;
  if (now - cpuTempCache.at < 900) return cpuTempCache.value;

  try {
    const data = await fetchJsonWithTimeout(LHM_DATA_URL, 2500);
    const sensor = findSensor(data, (node) => node.SensorId === CPU_TEMP_SENSOR_ID)
      || findSensor(data, (node) => node.Type === "Temperature" && /cpu package/i.test(node.Text || ""))
      || findSensor(data, (node) => node.Text === "CPU Package");
    const temp = sensor ? parseSensorNumber(sensor.RawValue || sensor.Value) : null;
    cpuTempCache = {
      at: now,
      value: temp,
      retryAfter: temp === null ? now + 5000 : now
    };
    return temp;
  } catch {
    cpuTempCache = { at: now, value: null, retryAfter: now + 5000 };
    return null;
  }
}

async function getCpuPowerWatts() {
  const now = Date.now();
  if (now < cpuPowerCache.retryAfter) return cpuPowerCache.value;
  if (now - cpuPowerCache.at < 900) return cpuPowerCache.value;

  try {
    const data = await fetchJsonWithTimeout(LHM_DATA_URL, 2500);
    const sensor = findSensor(data, (node) => node.SensorId === CPU_POWER_SENSOR_ID)
      || findSensor(data, (node) => node.Type === "Power" && /cpu package/i.test(node.Text || ""))
      || findSensor(data, (node) => node.Type === "Power" && /cpu/i.test(node.Text || ""));
    const watts = sensor ? parseSensorNumber(sensor.RawValue || sensor.Value) : null;
    cpuPowerCache = {
      at: now,
      value: watts,
      retryAfter: watts === null ? now + 5000 : now
    };
    return watts;
  } catch {
    cpuPowerCache = { at: now, value: null, retryAfter: now + 5000 };
    return null;
  }
}

async function fetchJsonWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`LibreHardwareMonitor returned ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function findSensor(node, predicate) {
  if (!node || typeof node !== "object") return null;
  if (predicate(node)) return node;
  for (const child of node.Children || []) {
    const found = findSensor(child, predicate);
    if (found) return found;
  }
  return null;
}

function parseSensorNumber(value) {
  const match = String(value || "").match(/-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const number = Number(match[0]);
  return Number.isFinite(number) ? Number(number.toFixed(1)) : null;
}

function getDeepSeekDaily(balance) {
  if (!balance.ok || !balance.data) {
    return { date: localDate(), baseline_at: null, items: [] };
  }

  const today = localDate();
  const current = balanceToMap(balance.data.balance_infos || []);
  let baseline = readBaseline();
  if (baseline.date !== today) {
    baseline = {
      date: today,
      baseline_at: new Date().toISOString(),
      balances: current
    };
    writeBaseline(baseline);
  }

  const currencies = Array.from(new Set([...Object.keys(baseline.balances || {}), ...Object.keys(current)]));
  return {
    date: baseline.date,
    baseline_at: baseline.baseline_at,
    items: currencies.map((currency) => {
      const initial = Number(baseline.balances?.[currency] || 0);
      const now = Number(current[currency] || 0);
      return {
        currency,
        initial: roundMoney(initial),
        current: roundMoney(now),
        used: roundMoney(initial - now)
      };
    })
  };
}

function readBaseline() {
  if (!fs.existsSync(BASELINE_FILE)) return { date: null, baseline_at: null, balances: {} };
  try {
    return JSON.parse(fs.readFileSync(BASELINE_FILE, "utf8"));
  } catch {
    return { date: null, baseline_at: null, balances: {} };
  }
}

function writeBaseline(baseline) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(BASELINE_FILE, JSON.stringify(baseline, null, 2));
}

function balanceToMap(infos) {
  const map = {};
  for (const item of infos) {
    map[item.currency] = Number(item.total_balance || 0);
  }
  return map;
}

function sendJson(response, status, data) {
  const body = JSON.stringify(data);
  response.writeHead(status, {
    "Content-Type": MIME[".json"],
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body)
  });
  response.end(body);
}

function sendStatic(request, response) {
  const url = new URL(request.url, `http://${request.headers.host}`);
  const pathname = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
  const target = path.normalize(path.join(PUBLIC_DIR, pathname));
  if (!target.startsWith(PUBLIC_DIR)) {
    response.writeHead(403);
    response.end("Forbidden");
    return;
  }
  fs.readFile(target, (error, content) => {
    if (error) {
      response.writeHead(404);
      response.end("Not found");
      return;
    }
    response.writeHead(200, {
      "Content-Type": MIME[path.extname(target)] || "application/octet-stream",
      "Cache-Control": "no-store"
    });
    response.end(content);
  });
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
    free_percent: total ? clampPercent((free / total) * 100) : 0
  };
}

function localDate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function roundMoney(value) {
  return Number(value.toFixed(4));
}

function maskKey(key) {
  if (!key) return "not configured";
  return `${key.slice(0, 5)}...${key.slice(-4)}`;
}

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, `http://${request.headers.host}`);
    if (request.method === "GET" && url.pathname === "/api/metrics") {
      sendJson(response, 200, await getMetrics());
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/system") {
      sendJson(response, 200, await getSystemMetrics());
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/drive") {
      sendJson(response, 200, await getDriveSnapshot());
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/deepseek") {
      sendJson(response, 200, await getDeepSeekSnapshot());
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/processes") {
      sendJson(response, 200, await getTopProcesses());
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/gpu") {
      const gpu = await getGpuMetrics();
      if (gpu) {
        sendJson(response, 200, gpu);
      } else {
        sendJson(response, 503, { error: "GPU metrics unavailable" });
      }
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/reports") {
      sendJson(response, 200, listReports());
      return;
    }
    if (request.method === "GET") {
      sendStatic(request, response);
      return;
    }
    sendJson(response, 405, { error: "Method not allowed" });
  } catch (error) {
    sendJson(response, 500, { error: error.message });
  }
});

function listReports() {
  if (!fs.existsSync(REPORTS_DIR)) return [];
  return fs.readdirSync(REPORTS_DIR)
    .filter(f => f.endsWith(".html"))
    .map(f => {
      const stat = fs.statSync(path.join(REPORTS_DIR, f));
      const match = f.match(/^report_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})\.html$/);
      const range = match ? `${match[1]} ${match[2].replace('-',':')}` : f;
      return { file: f, range, size: stat.size, generated: stat.mtime.toISOString() };
    })
    .sort((a, b) => b.generated.localeCompare(a.generated));
}

server.listen(PORT, () => {
  console.log(`Dashboard running at http://localhost:${PORT}`);
});
