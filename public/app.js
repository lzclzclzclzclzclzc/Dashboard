const cpuHistory = [];
const memoryHistory = [];

const $ = (id) => document.getElementById(id);

function bytes(value) {
  if (!value) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function money(value) {
  const number = Number(value || 0);
  return Number.isInteger(number) ? String(number) : number.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function setText(id, value) {
  $(id).textContent = value;
}

function setBar(id, percent) {
  $(id).style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function pushSample(list, value) {
  list.push(value);
  if (list.length > 60) list.shift();
}

function renderSpark(value) {
  const values = cpuHistory.slice(-28);
  $("cpuSpark").innerHTML = values
    .map((item) => `<span style="height:${Math.max(3, item)}%"></span>`)
    .join("");
}

function renderLineChart(canvas, values, color) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));

  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }

  ctx.clearRect(0, 0, width, height);
  ctx.lineWidth = 1 * dpr;
  ctx.strokeStyle = "rgba(21, 21, 21, 0.08)";
  for (let i = 0; i <= 4; i += 1) {
    const y = (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  if (values.length < 2) return;

  const padding = 10 * dpr;
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  const step = usableWidth / 59;
  const points = values.map((value, index) => {
    const x = padding + (60 - values.length + index) * step;
    const y = padding + usableHeight - (Math.max(0, Math.min(100, value)) / 100) * usableHeight;
    return { x, y };
  });

  const gradient = ctx.createLinearGradient(0, padding, 0, height - padding);
  gradient.addColorStop(0, `${color}33`);
  gradient.addColorStop(1, `${color}00`);

  ctx.beginPath();
  ctx.moveTo(points[0].x, height - padding);
  for (const point of points) ctx.lineTo(point.x, point.y);
  ctx.lineTo(points[points.length - 1].x, height - padding);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.lineWidth = 3 * dpr;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = color;
  ctx.stroke();
}

function renderSystem(data) {
  const cpu = data.cpu.percent;
  const memory = data.memory.percent;
  const cpuPower = data.cpu.power_watts;
  const hasCpuPower = typeof cpuPower === "number";
  pushSample(cpuHistory, cpu);
  pushSample(memoryHistory, memory);

  const cpuTemp = data.cpu.temp_celsius;
  const hasCpuTemp = typeof cpuTemp === "number";

  setText("cpuPercent", `${cpu}%`);
  setText("cpuTempRing", hasCpuTemp ? `${cpuTemp}°C` : "--°C");
  $("cpuTempRing").parentElement.style.setProperty("--value", hasCpuTemp ? Math.min(100, (cpuTemp / 100) * 100) : 0);
  setText("cpuPowerRing", hasCpuPower ? `${cpuPower}W` : "--W");
  $("cpuPowerRing").parentElement.style.setProperty("--value", hasCpuPower ? Math.min(100, (cpuPower / 40) * 100) : 0);
  setText("cpuModel", data.cpu.model);
  renderSpark(cpu);

  setText("memPercent", `${memory}%`);
  setText("memUsed", bytes(data.memory.used));
  setText("memTotal", bytes(data.memory.total));
  setBar("memBar", memory);

  setText("cpuChartValue", `${cpu}%`);
  setText("memChartValue", `${memory}%`);
  renderLineChart($("cpuChart"), cpuHistory, "#c78f2d");
  renderLineChart($("memoryChart"), memoryHistory, "#286f9b");
  setText("statusText", `System ${new Date(data.at).toLocaleTimeString("zh-CN")}`);
}

function renderDrive(data) {
  const drive = data.drive;
  setText("diskPercent", `${drive.free_percent}%`);
  setText("diskFree", bytes(drive.free));
  setText("diskTotal", bytes(drive.total));
  setBar("diskBar", drive.free_percent);
}

function renderBalance(deepseek) {
  const list = $("balanceList");
  const daily = $("dailyUsageList");
  const balance = deepseek.balance;
  setText("keyMask", deepseek.key_mask);

  if (!balance.ok || !balance.data) {
    setText("balanceStatus", "Error");
    list.innerHTML = `<div class="balance-item"><span>${balance.message || "Unable to read"}</span><b>--</b></div>`;
    daily.textContent = "--";
    document.querySelector(".pulse").className = "pulse bad";
    return;
  }

  setText("balanceStatus", balance.data.is_available ? "Available" : "Unavailable");
  const infos = balance.data.balance_infos || [];
  list.innerHTML = infos.length
    ? infos.map((item) => `
      <div class="balance-item">
        <span>${item.currency}</span>
        <b>${item.total_balance}</b>
      </div>
    `).join("")
    : `<div class="balance-item"><span>No balance details</span><b>--</b></div>`;

  const items = deepseek.daily?.items || [];
  daily.innerHTML = items.length
    ? items.map((item) => `
      <div class="daily-item">
        <span>${item.currency}</span>
        <b>${money(item.used)}</b>
      </div>
    `).join("")
    : "--";
  document.querySelector(".pulse").className = "pulse ok";
}

async function loadSystem() {
  try {
    const response = await fetch("/api/system", { cache: "no-store" });
    renderSystem(await response.json());
  } catch (error) {
    document.querySelector(".pulse").className = "pulse bad";
    setText("statusText", error.message);
  }
}

async function loadDrive() {
  try {
    const response = await fetch("/api/drive", { cache: "no-store" });
    renderDrive(await response.json());
  } catch (error) {
    setText("diskPercent", "--%");
    setText("diskFree", error.message);
  }
}

async function loadDeepSeek() {
  try {
    const response = await fetch("/api/deepseek", { cache: "no-store" });
    renderBalance(await response.json());
  } catch (error) {
    document.querySelector(".pulse").className = "pulse bad";
    setText("balanceStatus", "Error");
    $("balanceList").innerHTML = `<div class="balance-item"><span>${error.message}</span><b>--</b></div>`;
  }
}

function renderProcessTable(tbodyId, list) {
  const tbody = $(tbodyId);
  if (!list || list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5">No data</td></tr>';
    return;
  }
  tbody.innerHTML = list
    .map((proc, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td class="proc-name" title="${proc.Name}">${proc.Name}</td>
        <td>${proc.Id}</td>
        <td>${proc.CPU}</td>
        <td>${proc.MemMB}</td>
      </tr>
    `)
    .join("");
}

async function loadProcesses() {
  try {
    const response = await fetch("/api/processes", { cache: "no-store" });
    const data = await response.json();
    renderProcessTable("cpuProcBody", data.cpu);
    renderProcessTable("memProcBody", data.memory);
  } catch (error) {
    $("cpuProcBody").innerHTML = `<tr><td colspan="5">${error.message}</td></tr>`;
    $("memProcBody").innerHTML = `<tr><td colspan="5">${error.message}</td></tr>`;
  }
}

function refreshCharts() {
  renderLineChart($("cpuChart"), cpuHistory, "#c78f2d");
  renderLineChart($("memoryChart"), memoryHistory, "#286f9b");
}

async function loadReports() {
  try {
    const response = await fetch("/api/reports", { cache: "no-store" });
    const reports = await response.json();
    const currentFiles = [...document.querySelectorAll(".report-item")].map(el => el.dataset.file);
    const newFiles = reports.slice(0, 3).map(r => r.file);
    if (JSON.stringify(currentFiles) !== JSON.stringify(newFiles)) {
      renderReports(reports.slice(0, 3));
    }
  } catch (error) {
    $("reportsList").innerHTML = `<p class="muted">${error.message}</p>`;
  }
}

function renderReports(reports) {
  const list = $("reportsList");
  // Preserve open state before re-render
  const openFiles = new Set(
    [...document.querySelectorAll(".report-item.open")].map(el => el.dataset.file)
  );
  if (!reports || reports.length === 0) {
    list.innerHTML = '<p class="muted">No reports yet. Run telemetry-report skill to generate one.</p>';
    return;
  }
  list.innerHTML = reports.map((r, i) => {
    const date = new Date(r.generated);
    const label = date.toLocaleString("zh-CN", { month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" });
    const size = r.size > 1024*1024 ? `${(r.size/(1024*1024)).toFixed(1)} MB` : `${Math.round(r.size/1024)} KB`;
    const id = `report-${i}`;
    const wasOpen = openFiles.has(r.file);
    return `
      <div class="report-item${wasOpen ? ' open' : ''}" id="${id}" data-file="${r.file}">
        <button class="report-toggle" onclick="toggleReport('${id}','${r.file}')">
          <div class="report-meta">
            <span class="report-period">${label}</span>
            <span class="report-size">${size}</span>
          </div>
          <span class="toggle-icon">▼</span>
        </button>
        <div class="report-body">
          <iframe src="${wasOpen ? '/reports/' + r.file : 'about:blank'}" data-src="/reports/${r.file}"></iframe>
        </div>
      </div>
    `;
  }).join("");
}

function toggleReport(id, file) {
  const item = document.getElementById(id);
  const isOpen = item.classList.contains("open");
  const iframe = item.querySelector("iframe");

  if (isOpen) {
    item.classList.remove("open");
    return;
  }

  item.classList.add("open");
  if (iframe && iframe.dataset.src && iframe.src === "about:blank") {
    iframe.src = iframe.dataset.src;
  }
}

loadSystem();
loadDrive();
loadDeepSeek();
loadProcesses();
loadReports();

setInterval(loadSystem, 1000);
setInterval(loadDrive, 10000);
setInterval(loadDeepSeek, 60000);
setInterval(loadProcesses, 3000);
setInterval(loadReports, 30000);
window.addEventListener("resize", refreshCharts);
$("refreshReportsBtn")?.addEventListener("click", loadReports);
