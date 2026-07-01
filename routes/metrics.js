const os = require("os");
const { sendJson } = require("../lib/util");
const { getCpuPowerWatts } = require("../lib/lhm");
const { getSystemMetrics } = require("./system");
const { getDriveSnapshot } = require("./drive");
const { getDeepSeekSnapshot } = require("./deepseek");

async function getMetrics() {
  const [system, drive, deepseek] = await Promise.all([
    getSystemMetrics(),
    getDriveSnapshot(),
    getDeepSeekSnapshot(),
  ]);
  system.cpu.power_watts = system.cpu.power_watts ?? (await getCpuPowerWatts());
  return {
    ...system,
    drive: drive.drive,
    deepseek,
  };
}

async function handler(req, res) {
  sendJson(res, 200, await getMetrics());
}

module.exports = { handler, getMetrics };
