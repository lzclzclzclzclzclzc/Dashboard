const os = require("os");
const { sendJson, clampPercent } = require("../lib/util");
const { getCpuPowerWatts, getCpuTempCelsius } = require("../lib/lhm");

let previousCpu = readCpuSnapshot();

function readCpuSnapshot() {
  const cpus = os.cpus();
  const totals = cpus.map((cpu) => {
    const idle = cpu.times.idle;
    const total = Object.values(cpu.times).reduce((s, t) => s + t, 0);
    return { idle, total };
  });
  return { cpus: totals, at: Date.now() };
}

function getCpuUsage() {
  const current = readCpuSnapshot();
  const values = current.cpus.map((cpu, i) => {
    const prev = previousCpu.cpus[i] || cpu;
    const idleD = cpu.idle - prev.idle;
    const totalD = cpu.total - prev.total;
    return totalD > 0 ? 1 - idleD / totalD : 0;
  });
  previousCpu = current;
  const avg = values.reduce((s, v) => s + v, 0) / Math.max(values.length, 1);
  return {
    percent: clampPercent(avg * 100),
    cores: values.map((v) => clampPercent(v * 100)),
    model: os.cpus()[0]?.model || "Unknown CPU",
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
    percent: clampPercent((used / total) * 100),
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
      uptime: os.uptime(),
    },
    cpu,
    memory: getMemoryUsage(),
  };
}

async function handler(req, res) {
  sendJson(res, 200, await getSystemMetrics());
}

module.exports = { handler, getSystemMetrics };
