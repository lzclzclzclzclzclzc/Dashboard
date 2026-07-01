const config = require("./config");
const Cache = require("./cache");

const cache = new Cache();

async function fetchJsonWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`LHM returned ${response.status}`);
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
  const n = Number(match[0]);
  return Number.isFinite(n) ? Number(n.toFixed(1)) : null;
}

async function getCpuTempCelsius() {
  return cache.get("cpu-temp", 900, 5000, async () => {
    const data = await fetchJsonWithTimeout(config.LHM_DATA_URL, 2500);
    const sensor =
      findSensor(data, (n) => n.SensorId === config.CPU_TEMP_SENSOR_ID) ||
      findSensor(data, (n) => n.Type === "Temperature" && /cpu package/i.test(n.Text || "")) ||
      findSensor(data, (n) => n.Text === "CPU Package");
    return sensor ? parseSensorNumber(sensor.RawValue || sensor.Value) : null;
  });
}

async function getCpuPowerWatts() {
  return cache.get("cpu-power", 900, 5000, async () => {
    const data = await fetchJsonWithTimeout(config.LHM_DATA_URL, 2500);
    const sensor =
      findSensor(data, (n) => n.SensorId === config.CPU_POWER_SENSOR_ID) ||
      findSensor(data, (n) => n.Type === "Power" && /cpu package/i.test(n.Text || "")) ||
      findSensor(data, (n) => n.Type === "Power" && /cpu/i.test(n.Text || ""));
    return sensor ? parseSensorNumber(sensor.RawValue || sensor.Value) : null;
  });
}

module.exports = { getCpuTempCelsius, getCpuPowerWatts };
