# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm start          # Start the server (node server.js)
node server.js     # Equivalent — no build step required
```

There are no tests, linters, or bundlers. The entire stack is vanilla Node.js + vanilla JS with no transpilation.

## Architecture

**Claw Dashboard** is a local system monitoring dashboard. A Node.js HTTP server (`server.js`) exposes REST endpoints; a browser page (`public/`) polls those endpoints and renders metrics using HTML5 Canvas and plain DOM manipulation.

### Backend structure

`server.js` is now a thin router — it loads `.env`, registers routes from `routes/`, and serves static files. All business logic lives in two layers:

**`lib/`** — shared utilities:
| File | Purpose |
|---|---|
| `lib/config.js` | Single source of truth for all env vars and paths |
| `lib/cache.js` | `Cache` class: TTL + retry-after in-memory cache |
| `lib/lhm.js` | LibreHardwareMonitor fetch + sensor tree traversal (`getCpuTempCelsius`, `getCpuPowerWatts`) |
| `lib/util.js` | `sendJson`, `clampPercent`, `getDriveShape`, `localDate`, `roundMoney`, `maskKey` |

**`routes/`** — one file per API endpoint:
| Route | File | Data |
|---|---|---|
| `GET /api/system` | `routes/system.js` | CPU % per-core + memory + LHM power/temp |
| `GET /api/drive` | `routes/drive.js` | Disk free/total/% |
| `GET /api/deepseek` | `routes/deepseek.js` | Balance + today's spend |
| `GET /api/gpu` | `routes/gpu.js` | GPU util/memory/power/temp via `nvidia-smi` |
| `GET /api/metrics` | `routes/metrics.js` | All of the above combined |
| `GET /api/processes` | `routes/processes.js` | Top 10 by CPU, top 10 by memory |
| `GET /api/reports` | `routes/reports.js` | List of HTML report files in `public/reports/` |
| `GET /*` | server.js | Static files from `public/` |

**Metric sources:**
- CPU usage / memory → `os` module
- Disk usage → PowerShell `DriveInfo` (Windows) or `df` (Linux)
- CPU power + temperature → remote LibreHardwareMonitor JSON API (`LIBRE_HARDWARE_MONITOR_URL`), cached 900 ms via `lib/cache.js`
- GPU metrics → `nvidia-smi` (dedicated memory/util/power/temp) + Windows Performance Counters (shared memory), cached 900 ms
- Top processes → PowerShell `Get-Process` (Windows-only)
- DeepSeek balance → `api.deepseek.com`, daily spend calculated against a per-day baseline stored in `data/deepseek-baseline.json`

`logger.js` is `require()`d by `server.js` at startup. It runs a background loop (every 60 s) that calls the same internal metric functions and writes rows to `data/telemetry.db` (SQLite via `better-sqlite3`). Rows older than 30 days are auto-pruned.

### Frontend (`public/`)

Single-page app with no framework. `app.js` drives polling loops with `setInterval`:
- **1 s** → `/api/system` + `/api/gpu`
- **3 s** → `/api/processes`
- **10 s** → `/api/drive`
- **30 s** → `/api/reports`
- **60 s** → `/api/deepseek`

`renderLineChart(canvas, input, color, yMax)` accepts either a plain number array or a multi-series array `[{values, color}]`. All chart scaling uses an optional `yMax` ceiling (GPU power and memory use dynamic ceilings).

Reports are HTML files stored in `public/reports/`. The frontend lists the 3 most recent, renders them in collapsible `<iframe>` panels (lazy-loaded on expand).

### Environment variables (`.env` parsed manually in `server.js`)

```
PORT=3000
DEEPSEEK_API_KEY=sk-...             # balance card
LIBRE_HARDWARE_MONITOR_URL=http://<host>:8085/data.json
CPU_POWER_SENSOR_ID=/intelcpu/0/power/0
CPU_TEMP_SENSOR_ID=/intelcpu/0/temperature/18   # optional override
```

Copy `.env.example` to `.env` to configure.

### Key design constraints

- **Windows-first**: disk and process features use PowerShell; GPU uses `nvidia-smi` + Windows Performance Counters. Linux fallback only covers disk.
- **No WebSocket**: all updates are HTTP polling — adding push would require restructuring the server.
- **LHM sensor IDs are brittle**: sensor paths like `/intelcpu/0/temperature/18` are hardware-specific. Use `findSensor()` in `lib/lhm.js` to locate the correct ID from the LHM JSON tree when changing machines.
- **`data/` is runtime-only**: `telemetry.db` and `deepseek-baseline.json` are git-ignored and created at runtime.
- **`public/reports/` is runtime-only**: generated HTML reports are git-ignored.
- **Adding a new API route**: create `routes/foo.js` exporting `{ handler }`, then add one line to the route table in `server.js`. Config goes in `lib/config.js`, not inline.
