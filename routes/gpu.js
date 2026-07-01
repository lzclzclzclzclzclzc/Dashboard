const { execFile } = require("child_process");
const { promisify } = require("util");
const Cache = require("../lib/cache");
const { sendJson } = require("../lib/util");

const execFileAsync = promisify(execFile);
const cache = new Cache();

let gpuInstanceName = null;

async function discoverGpuInstance() {
  if (gpuInstanceName) return gpuInstanceName;
  try {
    const { stdout } = await execFileAsync("powershell", [
      "-NoProfile", "-Command",
      "(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples | Where-Object { $_.CookedValue -gt 0 } | ForEach-Object { $_.InstanceName } | Select-Object -First 1",
    ]);
    gpuInstanceName = stdout.trim();
    if (gpuInstanceName) console.log(`[gpu] discovered GPU perf instance: ${gpuInstanceName}`);
  } catch (err) {
    console.error(`[gpu] failed to discover GPU perf instance: ${err.message}`);
  }
  return gpuInstanceName;
}

async function getGpuMetrics() {
  return cache.get("gpu", 900, 5000, async () => {
    const [smiResult] = await Promise.all([
      execFileAsync("nvidia-smi", [
        "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,temperature.gpu",
        "--format=csv,noheader,nounits",
      ]),
      discoverGpuInstance(),
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
          `(Get-Counter "\\GPU Adapter Memory(${instance})\\Shared Usage" -ErrorAction SilentlyContinue).CounterSamples | Select-Object -ExpandProperty CookedValue`,
        ]);
        const rawShared = parseFloat(sharedOut.trim());
        if (Number.isFinite(rawShared)) {
          sharedMemMb = Math.round((rawShared / (1024 * 1024)) * 10) / 10;
        }
      } catch { /* shared memory unavailable */ }
    }

    return {
      at: new Date().toISOString(),
      name,
      utilization_gpu: utilGpu,
      utilization_memory: utilMem,
      memory: {
        dedicated_used_mb: memUsed,
        dedicated_total_mb: memTotal,
        shared_used_mb: sharedMemMb,
      },
      power: { draw_w: powerDraw, limit_w: powerLimit },
      temperature: tempGpu,
    };
  });
}

async function handler(req, res) {
  const gpu = await getGpuMetrics();
  if (gpu) sendJson(res, 200, gpu);
  else sendJson(res, 503, { error: "GPU metrics unavailable" });
}

module.exports = { handler, getGpuMetrics };
