const { execFile } = require("child_process");
const { promisify } = require("util");
const { sendJson } = require("../lib/util");

const execFileAsync = promisify(execFile);

async function getTopProcesses() {
  if (process.platform !== "win32") {
    return { cpu: [], memory: [], error: "Only supported on Windows" };
  }
  try {
    const cpuCmd = `Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 | ForEach-Object { [pscustomobject]@{Name=$_.ProcessName;Id=$_.Id;CPU=$([math]::Round($_.CPU,1));MemMB=$([math]::Round($_.WorkingSet64/1MB,1))} } | ConvertTo-Json -Compress`;
    const memCmd = `Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 | ForEach-Object { [pscustomobject]@{Name=$_.ProcessName;Id=$_.Id;CPU=$([math]::Round($_.CPU,1));MemMB=$([math]::Round($_.WorkingSet64/1MB,1))} } | ConvertTo-Json -Compress`;

    const [cpuResult, memResult] = await Promise.all([
      execFileAsync("powershell", ["-NoProfile", "-Command", cpuCmd]),
      execFileAsync("powershell", ["-NoProfile", "-Command", memCmd]),
    ]);

    const cpuList = JSON.parse(cpuResult.stdout.trim() || "[]");
    const memList = JSON.parse(memResult.stdout.trim() || "[]");
    return {
      cpu: Array.isArray(cpuList) ? cpuList : [],
      memory: Array.isArray(memList) ? memList : [],
    };
  } catch (error) {
    return { cpu: [], memory: [], error: error.message };
  }
}

async function handler(req, res) {
  sendJson(res, 200, await getTopProcesses());
}

module.exports = { handler, getTopProcesses };
