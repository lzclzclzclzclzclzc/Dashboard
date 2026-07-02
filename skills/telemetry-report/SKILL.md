---
name: telemetry-report
description: >
  Generate interactive HTML system health reports from the Dashboard telemetry SQLite database.
  The script extracts structured facts (stats, notable-event episodes with duration, top processes,
  hot/busy hours, data gaps); YOU then read those facts and write Chinese analysis prose that gets
  embedded in the final HTML. This yields context-aware diagnosis instead of hard-coded thresholds.
  Use when the user asks to review computer running status, system health, performance history,
  or generate a telemetry/dashboard report. Triggers: "运行报告", "系统报告", "telemetry report",
  "health report", "检查电脑", "查看运行状态", "分析log", "生成报告", "出个报告".
---

# Telemetry Report

Generate self-contained HTML reports from the Dashboard telemetry SQLite database.
The Python script does **data extraction only**; the calling agent (you) writes the diagnostic prose.
This keeps rare/subtle cases from being flattened by hard-coded `if temp > 85` rules.

## Database

Default path: `C:\Users\64379\Desktop\Claw\dashboard\data\telemetry.db`

Table `telemetry` columns: `ts`, `cpu_pct`, `cpu_temp`, `cpu_power`, `mem_pct`, `mem_used_gb`, `mem_total_gb`, `disk_free_gb`, `disk_total_gb`, `disk_pct`, `cpu_top10` (JSON), `mem_top10` (JSON), `ds_balance` (JSON), `ds_daily_used` (JSON), `gpu_util`, `gpu_mem_dedicated_mb`, `gpu_mem_shared_mb`, `gpu_mem_total_mb`, `gpu_power_w`, `gpu_temp`.

## Recommended workflow (agent-authored analysis)

**Step 1 — Extract facts (agent runs):**
```powershell
python scripts/generate_report.py --db <path> --range 24h --mode extract > facts.json
```

`facts.json` contains:
- `range`, `cpu`, `memory`, `disk`, `gpu` — statistical summaries (avg / min / max)
- `notable_events` — merged episodes with `start`, `end`, `duration_min`, `count`, `peak` sample, up to 3 context `samples`. Kinds: `cpu_hot`, `cpu_busy`, `mem_high`, `gpu_hot`, `gpu_idle_power`
- `hot_hours`, `busy_hours` — top 3 CPU-hot and CPU-busy hours
- `data_gaps` — sleep/off periods with ≥2 hr no-data
- `top_cpu_processes`, `top_memory_processes`

**Step 2 — Analyze (agent reads facts.json and writes analysis.json):**

Write two HTML fragments to `analysis.json`:
```json
{
  "overview_html": "<h2>📋 整体评估</h2><p>...Chinese prose...</p>",
  "anomaly_html":  "<h3>🔥 xxxx</h3><p>...</p><ul><li>...</li></ul>"
}
```

Guidelines for the analysis:
- **Focus on anomalies; skip if truly normal.** If nothing is wrong, say so briefly with `<p class="ok">✅ ...</p>`.
- **Use the data**, don't invent numbers. Every claim should cite `notable_events`, `hot_hours`, or a stat.
- **Diagnose scenarios**, don't restate metrics:
  - CPU 高温 + 低负载 → 硬件散热问题（硅脂、灰尘、风扇）
  - CPU 高温 + 高负载 → 正常应激，注意持续时长
  - GPU 高功耗 + 低利用率 → 后台 CUDA 或驱动功耗策略异常
  - 内存持续高占用 + 内存进程明确 → 建议针对性关闭 / 检查泄漏
- **Duration matters** — a 90°C peak for 1 minute ≠ 88°C for 2 hours. Reference `duration_min` in `notable_events`.
- **Cross-reference `hot_hours` with `busy_hours`** — hot but not busy is worse than hot & busy.
- **Use HTML classes:** `<span class="badge danger">严重</span>`, `<span class="badge warn">中等</span>`, `<span class="badge info">信息</span>`, `<span class="muted">...</span>`, `<p class="ok">...</p>`.
- **List items:** `<li>...</li>` inside `<ul>` — the report CSS styles them as cards.
- **Sections:** use `<h3>` inside `anomaly_html`; `overview_html` should lead with `<h2>📋 整体评估</h2>`.

**Step 3 — Render (agent runs):**
```powershell
python scripts/generate_report.py --db <path> --range 24h --mode render --analysis analysis.json
```

Report auto-saves to the dashboard's `public/reports/` directory (latest 3 kept) and appears at the bottom of the Dashboard frontend. The report header shows a "分析来源：agent" tag.

## Fallback (no agent involvement)

```powershell
python scripts/generate_report.py --db <path> --range 24h
```

Without `--mode`, the script uses a minimal rule-based summary and tags the report "分析来源：rule-based fallback". Use this only when running headlessly.

## Ranges

`1h`, `24h`, `7d`, `30d`, `custom` (with `--from "YYYY-MM-DD HH:MM"` and `--to "..."`).

## Report structure

1. **Summary Cards** — CPU/Memory/Disk stats, plus GPU cards when GPU data is present
2. **Overall Assessment** — `overview_html` from you (agent) or fallback
3. **Charts** — Chart.js line charts (CPU %, temp, power, memory, disk, GPU 4-tuple)
4. **Anomaly Analysis** — `anomaly_html` from you (agent) or fallback
5. **Process Top 10** — CPU and memory tables
6. **Hourly Breakdown** — with both mean and max CPU temperature per hour
