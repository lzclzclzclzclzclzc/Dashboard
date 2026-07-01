const { execFile } = require("child_process");
const { promisify } = require("util");
const { sendJson, getDriveShape } = require("../lib/util");

const execFileAsync = promisify(execFile);

async function getDriveUsage() {
  if (process.platform === "win32") {
    const { stdout } = await execFileAsync("powershell", [
      "-NoProfile", "-Command",
      "$d=[System.IO.DriveInfo]::new('C'); [pscustomobject]@{Size=$d.TotalSize;FreeSpace=$d.AvailableFreeSpace} | ConvertTo-Json -Compress",
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

async function getDriveSnapshot() {
  return { at: new Date().toISOString(), drive: await getDriveUsage() };
}

async function handler(req, res) {
  sendJson(res, 200, await getDriveSnapshot());
}

module.exports = { handler, getDriveSnapshot };
