---
name: telemetry-report
description: >
  Generate interactive HTML system health reports with Chinese anomaly analysis from the Dashboard telemetry SQLite database.
  Detects temperature spikes, CPU surges, memory pressure, and rapid metric changes with contextual analysis (what processes
  were running, overall system state at the time). Includes statistical summaries, hourly breakdowns, process top-lists,
  and actionable recommendations in Chinese. Use when the user asks to review computer running status, system health,
  performance history, or generate a telemetry/dashboard report. Triggers: "运行报告", "系统报告", "telemetry report",
  "health report", "检查电脑", "查看运行状态", "分析log", "生成报告", "出个报告".
---

# Telemetry Report

Generate self-contained HTML reports with **Chinese anomaly analysis** from the Dashboard telemetry SQLite database.

## Database

Default path: `C:\Users\64379\Desktop\Claw\dashboard\data\telemetry.db`

Table `telemetry` columns: `ts`, `cpu_pct`, `cpu_temp`, `cpu_power`, `mem_pct`, `mem_used_gb`, `mem_total_gb`, `disk_free_gb`, `disk_total_gb`, `disk_pct`, `cpu_top10` (JSON), `mem_top10` (JSON).

## Usage

```powershell
# Standard ranges (auto-saves to dashboard public/reports/):
python scripts/generate_report.py --db <path> --range 24h

# Custom range:
python scripts/generate_report.py --db <path> --range custom --from "2026-06-20 12:00" --to "2026-06-21 12:00"

# Force specific output path:
python scripts/generate_report.py --db <path> --range 24h --output /path/to/report.html
```

Ranges: `1h`, `24h`, `7d`, `30d`, `custom`.

When `--output` is omitted, the script auto-detects the dashboard's `public/reports/` directory
(from the DB path) and saves there, pruning old reports to keep only the latest 3.
Reports appear automatically at the bottom of the Dashboard frontend.

## Report Sections

1. **Summary Cards** — CPU usage, temperature, power, memory, disk free
2. **Overall Assessment** — Chinese prose evaluation of system health, with actionable recommendations
3. **Charts** — Interactive Chart.js line charts for CPU, temp, power, memory, disk
4. **Anomaly Analysis** — Multi-dimensional detection:
   - Temperature spikes (dynamic threshold based on 90th percentile, min 85°C)
   - CPU usage surges
   - Memory pressure events
   - Rapid metric changes (delta ≥10°C in 1 minute)
   - Each anomaly includes: timestamp, metrics, running processes, severity badge, and Chinese explanation
5. **Process Top 10** — CPU and memory tables
6. **Hourly Breakdown** — Temperature color-coded table

## Anomaly Detection Logic

- Uses dynamic thresholds (median + IQR / fixed minimums, whichever is stricter)
- Context extraction: for each anomaly, captures the process snapshot and system state at that moment
- Merges adjacent similar events to avoid noise
- Chinese analysis differentiates idle-hot (散热问题) vs load-hot (正常应激)
