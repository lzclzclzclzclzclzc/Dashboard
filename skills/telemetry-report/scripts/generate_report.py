#!/usr/bin/env python3
"""System Telemetry Report Generator

Two-stage workflow:
  1. `--mode extract`  → prints a JSON data summary to stdout for an LLM to analyze.
  2. `--mode render`   → given the LLM's analysis JSON via `--analysis`, builds the final HTML.

Legacy single-shot mode (`--mode auto`, default) still works: it uses rule-based
Chinese analysis as a fallback so the script remains runnable without an agent.

Usage:
  # Stage 1: extract structured facts for the agent
  python generate_report.py --db <path> --range 24h --mode extract > facts.json

  # Stage 2: after the agent writes analysis.json, render the report
  python generate_report.py --db <path> --range 24h --mode render --analysis analysis.json

  # Fallback: one-shot with rule-based analysis (original behavior)
  python generate_report.py --db <path> --range 24h
"""

import sqlite3, json, argparse, sys, os
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# ── Time range helpers ──────────────────────────────────────────────
def normalize_ts(ts):
    if ts is None: return None
    ts = ts.strip()
    return ts + ":00" if len(ts) == 16 else ts

def get_time_filter(args):
    if args.range == "custom":
        return normalize_ts(args.from_ts), normalize_ts(args.to_ts)
    now = datetime.now()
    delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
             "7d": timedelta(days=7), "30d": timedelta(days=30)}
    return (now - delta[args.range]).strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")

# ── DB queries ──────────────────────────────────────────────────────
def query_telemetry(db_path, from_ts, to_ts):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT ts, cpu_pct, cpu_temp, cpu_power, mem_pct, mem_used_gb, mem_total_gb,
               disk_free_gb, disk_total_gb, disk_pct, cpu_top10, mem_top10,
               gpu_util, gpu_mem_dedicated_mb, gpu_mem_shared_mb, gpu_mem_total_mb,
               gpu_power_w, gpu_temp
        FROM telemetry WHERE ts >= ? AND ts < ? ORDER BY ts
    """, (from_ts, to_ts))
    rows = cur.fetchall()

    cur.execute("""
        SELECT
            COUNT(*) samples,
            ROUND(MIN(cpu_pct),1) cpu_min,   ROUND(AVG(cpu_pct),1) cpu_avg,   ROUND(MAX(cpu_pct),1) cpu_max,
            ROUND(MIN(cpu_temp),1) temp_min, ROUND(AVG(cpu_temp),1) temp_avg, ROUND(MAX(cpu_temp),1) temp_max,
            ROUND(MIN(cpu_power),1) pwr_min, ROUND(AVG(cpu_power),1) pwr_avg, ROUND(MAX(cpu_power),1) pwr_max,
            ROUND(MIN(mem_pct),1) mem_min,   ROUND(AVG(mem_pct),1) mem_avg,   ROUND(MAX(mem_pct),1) mem_max,
            ROUND(AVG(mem_used_gb),1) mem_used, ROUND(AVG(mem_total_gb),1) mem_total,
            ROUND(AVG(disk_free_gb),1) disk_free, ROUND(AVG(disk_total_gb),1) disk_total,
            ROUND(MIN(disk_free_gb),1) disk_min, ROUND(MAX(disk_free_gb),1) disk_max,
            ROUND(AVG(gpu_util),1) gpu_util_avg,   ROUND(MAX(gpu_util),1) gpu_util_max,
            ROUND(AVG(gpu_power_w),1) gpu_pwr_avg, ROUND(MAX(gpu_power_w),1) gpu_pwr_max,
            ROUND(AVG(gpu_temp),1) gpu_temp_avg,   ROUND(MAX(gpu_temp),1) gpu_temp_max,
            ROUND(AVG(gpu_mem_dedicated_mb),0) gpu_mem_avg, ROUND(MAX(gpu_mem_total_mb),0) gpu_mem_total
        FROM telemetry WHERE ts >= ? AND ts < ?
    """, (from_ts, to_ts))
    stats = dict(cur.fetchone())

    cur.execute("""
        SELECT strftime('%Y-%m-%d %H:00',ts) hour, COUNT(*) n,
               ROUND(AVG(cpu_pct),1)   cpu,      ROUND(MAX(cpu_pct),1)   cpu_max,
               ROUND(AVG(cpu_temp),1)  temp,     ROUND(MAX(cpu_temp),1)  temp_max,
               ROUND(AVG(cpu_power),1) pwr,      ROUND(AVG(mem_pct),1)   mem,
               ROUND(AVG(gpu_util),1)  gpu_util, ROUND(AVG(gpu_temp),1)  gpu_temp,
               ROUND(MAX(gpu_temp),1)  gpu_temp_max
        FROM telemetry WHERE ts >= ? AND ts < ? GROUP BY hour ORDER BY hour
    """, (from_ts, to_ts))
    hourly = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, stats, hourly

# ── Process helpers ─────────────────────────────────────────────────
def parse_top10(row, field):
    try: return json.loads(row[field]) if row[field] else []
    except: return []

def aggregate_processes(rows, field, top_n=10):
    cpu_c, mem_c = Counter(), Counter()
    for row in rows:
        for p in parse_top10(row, field):
            name = p.get("Name", "Unknown")
            cpu_c[name] = max(cpu_c.get(name, 0), p.get("CPU", 0))
            mem_c[name] = max(mem_c.get(name, 0), p.get("MemMB", 0))
    return cpu_c.most_common(top_n), mem_c.most_common(top_n)

def process_snapshot(row):
    procs = parse_top10(row, "cpu_top10")
    return "、".join(f"{p.get('Name','?')}({p.get('CPU',0):.0f}s)" for p in procs[:3]) if procs else "无进程数据"

# ── Event episode building (data extraction, no verdict) ─────────────
def extract_events(rows):
    """Extract raw threshold crossings — objective facts, no judgment.
    The agent decides what these mean."""
    events = []
    if not rows: return events

    def pct(vals, p):
        if not vals: return 0
        s = sorted(vals)
        return s[max(0, int(len(s) * p / 100) - 1)]

    temp_vals = [r["cpu_temp"] for r in rows if r["cpu_temp"] is not None]
    cpu_vals  = [r["cpu_pct"]  for r in rows if r["cpu_pct"]  is not None]
    mem_vals  = [r["mem_pct"]  for r in rows if r["mem_pct"]  is not None]
    gtemp_vals = [r["gpu_temp"] for r in rows if r["gpu_temp"] is not None]

    # Thresholds are loose — extract "notable" points; agent judges severity.
    t_th  = min(max(pct(temp_vals, 85), 75), 100)
    c_th  = min(max(pct(cpu_vals, 85), 25), 90)
    m_th  = max(pct(mem_vals, 85), 60)
    gt_th = min(max(pct(gtemp_vals, 85), 80), 105)

    for row in rows:
        if row["cpu_temp"] is not None and row["cpu_temp"] >= t_th:
            events.append({"ts": row["ts"], "kind": "cpu_hot",
                           "cpu_temp": row["cpu_temp"], "cpu_pct": row["cpu_pct"],
                           "cpu_power": row["cpu_power"], "mem_pct": row["mem_pct"],
                           "procs": process_snapshot(row)})
        if row["cpu_pct"] is not None and row["cpu_pct"] >= c_th:
            events.append({"ts": row["ts"], "kind": "cpu_busy",
                           "cpu_pct": row["cpu_pct"], "cpu_temp": row["cpu_temp"],
                           "cpu_power": row["cpu_power"], "mem_pct": row["mem_pct"],
                           "procs": process_snapshot(row)})
        if row["mem_pct"] is not None and row["mem_pct"] >= m_th:
            mem_procs = parse_top10(row, "mem_top10")
            top3 = [{"name": p.get("Name"), "mb": p.get("MemMB", 0)} for p in mem_procs[:3]]
            events.append({"ts": row["ts"], "kind": "mem_high",
                           "mem_pct": row["mem_pct"], "cpu_pct": row["cpu_pct"],
                           "mem_procs": top3})
        if row["gpu_temp"] is not None and row["gpu_temp"] >= gt_th:
            events.append({"ts": row["ts"], "kind": "gpu_hot",
                           "gpu_temp": row["gpu_temp"], "gpu_util": row["gpu_util"],
                           "gpu_power": row["gpu_power_w"], "cpu_pct": row["cpu_pct"]})
        if (row["gpu_power_w"] is not None and row["gpu_util"] is not None
                and row["gpu_power_w"] > 30 and row["gpu_util"] < 5):
            events.append({"ts": row["ts"], "kind": "gpu_idle_power",
                           "gpu_power": row["gpu_power_w"], "gpu_util": row["gpu_util"],
                           "gpu_temp": row["gpu_temp"]})

    # Merge into episodes with duration
    return merge_episodes(events)

def merge_episodes(events, gap_min=5):
    """Group same-kind events within `gap_min` minutes into episodes with duration."""
    if not events: return []
    by_kind = defaultdict(list)
    for e in events:
        by_kind[e["kind"]].append(e)

    episodes = []
    for kind, evs in by_kind.items():
        evs = sorted(evs, key=lambda x: x["ts"])
        cur = None
        for e in evs:
            if cur is None:
                cur = {"kind": kind, "start": e["ts"], "end": e["ts"],
                       "count": 1, "peak": e, "samples": [e]}
            else:
                try:
                    prev_dt = datetime.strptime(cur["end"], "%Y-%m-%d %H:%M:%S")
                    cur_dt  = datetime.strptime(e["ts"],  "%Y-%m-%d %H:%M:%S")
                    gap = (cur_dt - prev_dt).total_seconds() / 60
                except:
                    gap = 999
                if gap <= gap_min:
                    cur["end"] = e["ts"]; cur["count"] += 1
                    cur["samples"].append(e)
                    # peak by relevant metric
                    peak_key = {"cpu_hot":"cpu_temp","cpu_busy":"cpu_pct","mem_high":"mem_pct",
                                "gpu_hot":"gpu_temp","gpu_idle_power":"gpu_power"}[kind]
                    if e.get(peak_key, 0) > cur["peak"].get(peak_key, 0):
                        cur["peak"] = e
                else:
                    episodes.append(_finalize(cur)); cur = {"kind": kind, "start": e["ts"],
                        "end": e["ts"], "count": 1, "peak": e, "samples": [e]}
        if cur: episodes.append(_finalize(cur))

    episodes.sort(key=lambda ep: (-ep["duration_min"], ep["start"]))
    return episodes

def _finalize(ep):
    try:
        s = datetime.strptime(ep["start"], "%Y-%m-%d %H:%M:%S")
        e = datetime.strptime(ep["end"],   "%Y-%m-%d %H:%M:%S")
        ep["duration_min"] = max(1, int((e - s).total_seconds() / 60))
    except:
        ep["duration_min"] = ep["count"]
    # keep only up to 3 sample points for context (start, mid, end)
    if len(ep["samples"]) > 3:
        mid = len(ep["samples"]) // 2
        ep["samples"] = [ep["samples"][0], ep["samples"][mid], ep["samples"][-1]]
    return ep

def detect_data_gaps(hourly, min_gap_hours=2):
    gaps = []
    for i in range(1, len(hourly)):
        try:
            prev = datetime.strptime(hourly[i-1]["hour"], "%Y-%m-%d %H:%M")
            curr = datetime.strptime(hourly[i]["hour"],   "%Y-%m-%d %H:%M")
            hrs = (curr - prev).total_seconds() / 3600
            if hrs >= min_gap_hours:
                gaps.append({"from": hourly[i-1]["hour"], "to": hourly[i]["hour"],
                             "hours": round(hrs, 1)})
        except: pass
    return gaps

# ── Facts JSON (fed to the agent) ────────────────────────────────────
def build_facts(rows, stats, hourly, from_ts, to_ts):
    duration_h = (datetime.strptime(to_ts, "%Y-%m-%d %H:%M:%S") -
                  datetime.strptime(from_ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600

    cpu_procs, _ = aggregate_processes(rows, "cpu_top10")
    _, mem_procs = aggregate_processes(rows, "mem_top10")
    episodes = extract_events(rows)
    gaps = detect_data_gaps(hourly)

    has_gpu = any(r["gpu_util"] is not None for r in rows)

    # Pick hot / busy hours (agent can reference these)
    hot_hours = sorted([h for h in hourly if h.get("temp")],
                      key=lambda h: h["temp"], reverse=True)[:3]
    busy_hours = sorted([h for h in hourly if h.get("cpu")],
                       key=lambda h: h["cpu"], reverse=True)[:3]

    return {
        "range": {"from": from_ts, "to": to_ts, "duration_hours": round(duration_h, 2),
                  "samples": stats["samples"]},
        "cpu": {
            "usage_pct": {"avg": stats["cpu_avg"], "min": stats["cpu_min"], "max": stats["cpu_max"]},
            "temp_c":    {"avg": stats["temp_avg"], "min": stats["temp_min"], "max": stats["temp_max"]},
            "power_w":   {"avg": stats["pwr_avg"], "min": stats["pwr_min"], "max": stats["pwr_max"]},
        },
        "memory": {"pct": {"avg": stats["mem_avg"], "min": stats["mem_min"], "max": stats["mem_max"]},
                   "used_gb_avg": stats["mem_used"], "total_gb": stats["mem_total"]},
        "disk":   {"free_gb_avg": stats["disk_free"], "free_gb_min": stats["disk_min"],
                   "free_gb_max": stats["disk_max"], "total_gb": stats["disk_total"]},
        "gpu": (None if not has_gpu else {
            "util_pct":  {"avg": stats["gpu_util_avg"], "max": stats["gpu_util_max"]},
            "power_w":   {"avg": stats["gpu_pwr_avg"], "max": stats["gpu_pwr_max"]},
            "temp_c":    {"avg": stats["gpu_temp_avg"], "max": stats["gpu_temp_max"]},
            "memory_mb": {"avg": stats["gpu_mem_avg"], "total": stats["gpu_mem_total"]},
        }),
        "notable_events": episodes,
        "hot_hours":  [{"hour": h["hour"], "cpu_temp": h["temp"], "cpu_pct": h["cpu"]} for h in hot_hours],
        "busy_hours": [{"hour": h["hour"], "cpu_pct": h["cpu"], "cpu_temp": h["temp"]} for h in busy_hours],
        "data_gaps": gaps,
        "top_cpu_processes":    [{"name": n, "peak_cpu_seconds": round(v, 0)} for n, v in cpu_procs],
        "top_memory_processes": [{"name": n, "peak_mem_mb":     round(v, 0)} for n, v in mem_procs],
    }

# ── Rule-based fallback analysis (used when no --analysis provided) ─
def fallback_analysis(facts):
    """Minimal descriptive fallback. Prefer LLM analysis via --analysis."""
    overall = ['<h2>📋 整体评估</h2>']
    cpu = facts["cpu"]
    mem = facts["memory"]
    r = facts["range"]

    overall.append(f'<p>本报告覆盖 <strong>{r["from"]} — {r["to"]}</strong>（{r["duration_hours"]:.1f} 小时，{r["samples"]} 条采样）。</p>')
    overall.append(f'<p>CPU 平均使用率 {cpu["usage_pct"]["avg"]}%（峰值 {cpu["usage_pct"]["max"]}%），'
                   f'平均温度 {cpu["temp_c"]["avg"]}°C（峰值 {cpu["temp_c"]["max"]}°C），'
                   f'平均功耗 {cpu["power_w"]["avg"]}W。</p>')
    overall.append(f'<p>内存平均占用 {mem["pct"]["avg"]}%，磁盘可用 {facts["disk"]["free_gb_avg"]} GB。</p>')
    if facts.get("gpu"):
        g = facts["gpu"]
        overall.append(f'<p>GPU 平均利用率 {g["util_pct"]["avg"]}%，平均温度 {g["temp_c"]["avg"]}°C，'
                       f'平均功耗 {g["power_w"]["avg"]}W。</p>')
    overall.append('<p class="muted">💡 未提供 <code>--analysis</code>，使用规则化摘要。'
                   '如需专业分析，请先 <code>--mode extract</code> 交由 LLM 分析后再 <code>--mode render --analysis &lt;json&gt;</code>。</p>')

    events = facts["notable_events"]
    if not events:
        anal = '<p class="ok">✅ 未检出明显异常时段。</p>'
    else:
        kind_names = {"cpu_hot":"CPU 高温","cpu_busy":"CPU 高负载","mem_high":"内存高占用",
                      "gpu_hot":"GPU 高温","gpu_idle_power":"GPU 空转高功耗"}
        by_kind = defaultdict(list)
        for ep in events: by_kind[ep["kind"]].append(ep)
        parts = []
        for kind, eps in by_kind.items():
            parts.append(f'<h3>{kind_names.get(kind, kind)}（{len(eps)} 个时段）</h3><ul>')
            for ep in eps[:5]:
                pk = ep["peak"]
                parts.append(f'<li>{ep["start"]} 起，持续 {ep["duration_min"]} 分钟'
                             f'<br><span class="muted">{json.dumps(pk, ensure_ascii=False)[:200]}</span></li>')
            parts.append('</ul>')
        anal = '\n'.join(parts)

    return {"overview_html": '\n'.join(overall), "anomaly_html": anal}

# ── HTML render ─────────────────────────────────────────────────────
def build_html(rows, stats, hourly, from_ts, to_ts, analysis):
    duration = (datetime.strptime(to_ts, "%Y-%m-%d %H:%M:%S") -
                datetime.strptime(from_ts, "%Y-%m-%d %H:%M:%S"))
    duration_hours = duration.total_seconds() / 3600

    def safe(v): return v if v is not None else "null"
    timestamps   = [r["ts"] for r in rows]
    cpu_arr      = [safe(r["cpu_pct"]) for r in rows]
    temp_arr     = [safe(r["cpu_temp"]) for r in rows]
    pwr_arr      = [safe(r["cpu_power"]) for r in rows]
    mem_arr      = [safe(r["mem_pct"]) for r in rows]
    disk_arr     = [safe(r["disk_free_gb"]) for r in rows]
    gpu_util_arr = [safe(r["gpu_util"]) for r in rows]
    gpu_pwr_arr  = [safe(r["gpu_power_w"]) for r in rows]
    gpu_ded_arr  = [safe(r["gpu_mem_dedicated_mb"]) for r in rows]
    gpu_temp_arr = [safe(r["gpu_temp"]) for r in rows]

    has_gpu = any(r["gpu_util"] is not None for r in rows)

    cpu_procs, _     = aggregate_processes(rows, "cpu_top10")
    _, mem_consumers = aggregate_processes(rows, "mem_top10")

    overview_html = analysis.get("overview_html", "<p class='muted'>无分析</p>")
    anomaly_html  = analysis.get("anomaly_html",  "<p class='muted'>无分析</p>")

    hourly_rows = ""
    for h in hourly:
        t  = h.get("temp") or 0
        tm = h.get("temp_max") or 0
        gt = h.get("gpu_temp") or 0
        t_color  = "var(--danger)" if t  >= 85 else ("var(--warn)" if t  >= 70 else "var(--text)")
        gt_color = "var(--danger)" if gt >= 90 else ("var(--warn)" if gt >= 75 else "var(--text)")
        gpu_cells = f'<td style="color:{gt_color};font-weight:600">{gt}°C</td><td>{h.get("gpu_util","--")}%</td>' if has_gpu else ""
        hourly_rows += (
            f'<tr><td>{h["hour"]}</td><td>{h["n"]}</td><td>{h["cpu"]}%</td>'
            f'<td style="color:{t_color};font-weight:600">{t}°C</td>'
            f'<td style="color:var(--muted);font-size:11px">{tm}°C</td>'
            f'<td>{h["pwr"]}W</td><td>{h["mem"]}%</td>{gpu_cells}</tr>'
        )

    mem_total     = stats.get("mem_total", 32) or 32
    gpu_mem_total = stats.get("gpu_mem_total", 0) or 0
    gpu_mem_avg   = stats.get("gpu_mem_avg", 0) or 0

    gpu_summary_cards = ""
    if has_gpu:
        gpu_mem_str = (f"{gpu_mem_avg/1024:.1f} / {gpu_mem_total/1024:.1f} GB"
                       if gpu_mem_total else f"{gpu_mem_avg:.0f} MB")
        gpu_summary_cards = f"""
<div class="card"><div class="label">GPU 利用率</div><div class="value" style="color:var(--gpu-util)">{stats.get('gpu_util_avg','--')}%</div><div class="sub">峰值 {stats.get('gpu_util_max','--')}%</div></div>
<div class="card"><div class="label">GPU 功耗</div><div class="value" style="color:var(--gpu-pwr)">{stats.get('gpu_pwr_avg','--')}W</div><div class="sub">峰值 {stats.get('gpu_pwr_max','--')}W</div></div>
<div class="card"><div class="label">GPU 温度</div><div class="value" style="color:var(--gpu-temp)">{stats.get('gpu_temp_avg','--')}°C</div><div class="sub">峰值 {stats.get('gpu_temp_max','--')}°C</div></div>
<div class="card"><div class="label">GPU 显存</div><div class="value" style="color:var(--gpu-mem)">{gpu_mem_str}</div></div>"""

    gpu_charts_html = ""
    if has_gpu:
        gpu_charts_html = """
<div class="grid-2">
<div class="chart-section"><h2>GPU 利用率（%）</h2><div class="chart-wrap"><canvas id="chartGpuUtil"></canvas></div></div>
<div class="chart-section"><h2>GPU 功耗（W）</h2><div class="chart-wrap"><canvas id="chartGpuPwr"></canvas></div></div>
<div class="chart-section"><h2>GPU 专用显存（MB）</h2><div class="chart-wrap"><canvas id="chartGpuMem"></canvas></div></div>
<div class="chart-section"><h2>GPU 温度（°C）</h2><div class="chart-wrap"><canvas id="chartGpuTemp"></canvas></div></div>
</div>"""

    gpu_hourly_header = "<th>GPU 温度</th><th>GPU%</th>" if has_gpu else ""
    gpu_chart_js = ""
    if has_gpu:
        gpu_chart_js = f"""
new Chart(document.getElementById('chartGpuUtil'),{{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(gpu_util_arr)},borderColor:'#58e6d9'}}]}},options:o('#58e6d9','%',100)}});
new Chart(document.getElementById('chartGpuPwr'),{{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(gpu_pwr_arr)},borderColor:'#e85d8a'}}]}},options:o('#e85d8a','W')}});
new Chart(document.getElementById('chartGpuMem'),{{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(gpu_ded_arr)},borderColor:'#c778dd'}}]}},options:o('#c778dd','MB')}});
new Chart(document.getElementById('chartGpuTemp'),{{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(gpu_temp_arr)},borderColor:'#e06040'}}]}},options:o('#e06040','°C')}});"""

    analysis_source = analysis.get("_source", "rule-based")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>System Telemetry Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;--accent:#58a6ff;
  --danger:#f85149;--warn:#d2991d;--ok:#3fb950;--temp:#e06040;--cpu:#58a6ff;--pwr:#d2991d;--mem:#7c3aed;--disk:#3fb950;
  --gpu-util:#58e6d9;--gpu-pwr:#e85d8a;--gpu-mem:#c778dd;--gpu-temp:#e06040}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;line-height:1.6}}
.header{{text-align:center;margin-bottom:32px}}
.header h1{{font-size:28px;color:var(--accent)}}
.header .sub{{color:var(--muted);margin-top:6px;font-size:14px}}
.header .tag{{display:inline-block;padding:2px 10px;background:var(--card);border:1px solid var(--border);border-radius:999px;font-size:11px;color:var(--muted);margin-top:8px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:32px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px}}
.card .label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.card .value{{font-size:28px;font-weight:700;margin:6px 0 2px}}
.card .sub{{font-size:12px;color:var(--muted)}}
.analysis{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:24px;margin-bottom:32px}}
.analysis h2{{color:var(--accent);margin-bottom:16px;font-size:20px}}
.analysis h3{{color:var(--warn);margin:20px 0 10px;font-size:16px}}
.analysis h3:first-child{{margin-top:0}}
.analysis p{{margin:8px 0;font-size:14px}}
.analysis ul{{padding-left:0;margin:8px 0}}
.analysis li{{margin:8px 0;font-size:13px;list-style:none;padding:8px 12px;background:var(--bg);border-radius:6px;border-left:3px solid var(--border)}}
.analysis li:hover{{border-left-color:var(--accent)}}
.analysis .muted{{color:var(--muted);font-size:12px}}
.analysis code{{background:var(--bg);padding:1px 6px;border-radius:4px;font-size:12px;color:var(--gpu-util)}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:6px}}
.badge.danger{{background:var(--danger);color:#fff}}
.badge.warn{{background:var(--warn);color:#000}}
.badge.info{{background:var(--accent);color:#fff}}
.chart-section{{margin-bottom:32px}}
.chart-section h2{{font-size:16px;color:var(--accent);margin-bottom:12px}}
.chart-wrap{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;position:relative;height:280px}}
.chart-wrap canvas{{width:100%!important;height:100%!important}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:900px){{.grid-2{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}}
th{{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase}}
tr:hover td{{background:rgba(255,255,255,0.02)}}
.footer{{text-align:center;color:var(--muted);font-size:12px;margin-top:32px;padding-top:16px;border-top:1px solid var(--border)}}
.ok{{color:var(--ok)}}
</style></head>
<body>

<div class="header">
<h1>System Telemetry Report</h1>
<div class="sub">{from_ts} — {to_ts} &nbsp;·&nbsp; {duration_hours:.1f} 小时 &nbsp;·&nbsp; {stats['samples']} 条记录</div>
<div class="tag">分析来源：{analysis_source}</div>
</div>

<div class="cards">
<div class="card"><div class="label">CPU 平均使用率</div><div class="value" style="color:var(--cpu)">{stats['cpu_avg']}%</div><div class="sub">最低 {stats['cpu_min']}% · 峰值 {stats['cpu_max']}%</div></div>
<div class="card"><div class="label">CPU 温度</div><div class="value" style="color:var(--temp)">{stats['temp_avg']}°C</div><div class="sub">最低 {stats['temp_min']}°C · 峰值 {stats['temp_max']}°C</div></div>
<div class="card"><div class="label">CPU 功耗</div><div class="value" style="color:var(--pwr)">{stats['pwr_avg']}W</div><div class="sub">峰值 {stats['pwr_max']}W</div></div>
<div class="card"><div class="label">内存占用</div><div class="value" style="color:var(--mem)">{stats['mem_avg']}%</div><div class="sub">{stats['mem_used']} / {mem_total:.0f} GB 均值</div></div>
<div class="card"><div class="label">磁盘可用</div><div class="value" style="color:var(--disk)">{stats['disk_free']} GB</div><div class="sub">共 {stats['disk_total']} GB · 最低 {stats['disk_min']} GB</div></div>
{gpu_summary_cards}
</div>

<div class="analysis">
{overview_html}
</div>

<div class="grid-2">
<div class="chart-section"><h2>CPU 使用率（%）</h2><div class="chart-wrap"><canvas id="chartCpu"></canvas></div></div>
<div class="chart-section"><h2>CPU 温度（°C）</h2><div class="chart-wrap"><canvas id="chartTemp"></canvas></div></div>
<div class="chart-section"><h2>CPU 功耗（W）</h2><div class="chart-wrap"><canvas id="chartPower"></canvas></div></div>
<div class="chart-section"><h2>内存使用率（%）</h2><div class="chart-wrap"><canvas id="chartMem"></canvas></div></div>
</div>
<div class="chart-section"><h2>磁盘可用空间（GB）</h2><div class="chart-wrap"><canvas id="chartDisk"></canvas></div></div>

{gpu_charts_html}

<div class="analysis">
<h2>🔍 异常事件详细分析</h2>
{anomaly_html}
</div>

<div class="grid-2">
<div class="chart-section"><h2>CPU 使用最多的进程 Top 10</h2>
<table><tr><th>进程名</th><th>最高 CPU 时间（s）</th></tr>
{''.join(f'<tr><td>{n}</td><td>{v:.0f}</td></tr>' for n,v in cpu_procs[:10]) or '<tr><td colspan=2 style="color:var(--muted)">无数据</td></tr>'}
</table></div>
<div class="chart-section"><h2>内存占用最多的进程 Top 10</h2>
<table><tr><th>进程名</th><th>最高内存（MB）</th></tr>
{''.join(f'<tr><td>{n}</td><td>{v:.0f}</td></tr>' for n,v in mem_consumers[:10]) or '<tr><td colspan=2 style="color:var(--muted)">无数据</td></tr>'}
</table></div>
</div>

<div class="chart-section"><h2>逐小时汇总</h2>
<table><tr><th>时段</th><th>样本数</th><th>CPU%</th><th>CPU 均温</th><th>CPU 峰温</th><th>CPU 功耗</th><th>内存%</th>{gpu_hourly_header}</tr>
{hourly_rows}
</table></div>

<div class="footer">由 telemetry-report skill 生成 &nbsp;·&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

<script>
const ts={json.dumps(timestamps)};
const o=(c,u,m)=>({{responsive:!0,maintainAspectRatio:!1,plugins:{{legend:{{display:!1}}}},scales:{{x:{{ticks:{{color:'#8b949e',maxTicksLimit:20,autoSkip:!0,font:{{size:11}}}}}},y:{{ticks:{{color:'#8b949e',font:{{size:11}}}},max:m,min:0,grid:{{color:'#21262d'}}}}}},elements:{{point:{{radius:0}},line:{{borderWidth:1.5,borderColor:c}}}}}});
new Chart(document.getElementById('chartCpu'),  {{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(cpu_arr)}, borderColor:'#58a6ff'}}]}},options:o('#58a6ff','%',100)}});
new Chart(document.getElementById('chartTemp'), {{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(temp_arr)},borderColor:'#e06040'}}]}},options:o('#e06040','°C')}});
new Chart(document.getElementById('chartPower'),{{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(pwr_arr)}, borderColor:'#d2991d'}}]}},options:o('#d2991d','W')}});
new Chart(document.getElementById('chartMem'),  {{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(mem_arr)}, borderColor:'#7c3aed'}}]}},options:o('#7c3aed','%',100)}});
new Chart(document.getElementById('chartDisk'), {{type:'line',data:{{labels:ts,datasets:[{{data:{json.dumps(disk_arr)},borderColor:'#3fb950'}}]}},options:o('#3fb950','GB')}});
{gpu_chart_js}
</script>
</body></html>"""
    return html

# ── Utilities ───────────────────────────────────────────────────────
def prune_old_reports(reports_dir, keep=3):
    if not os.path.isdir(reports_dir): return
    files = sorted([f for f in os.listdir(reports_dir) if f.endswith(".html")],
                   key=lambda f: os.path.getmtime(os.path.join(reports_dir, f)), reverse=True)
    for old in files[keep:]:
        os.remove(os.path.join(reports_dir, old))
        print(f"Pruned old report: {old}", file=sys.stderr)

# ── Main ────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Telemetry report — two-stage extract/render, or one-shot fallback")
    p.add_argument("--db", required=True, help="Path to telemetry.db")
    p.add_argument("--range", choices=["1h","24h","7d","30d","custom"], default="24h")
    p.add_argument("--from", dest="from_ts")
    p.add_argument("--to",   dest="to_ts")
    p.add_argument("--mode", choices=["extract", "render", "auto"], default="auto",
                   help="extract: emit facts JSON for the agent; render: build HTML with --analysis; auto: fallback rules")
    p.add_argument("--analysis", help="Path to analysis JSON produced by the agent (required when --mode render)")
    p.add_argument("--output", "-o", default=None)
    args = p.parse_args()

    from_ts, to_ts = get_time_filter(args)
    rows, stats, hourly = query_telemetry(args.db, from_ts, to_ts)

    if not rows:
        print(f"No data found in range {from_ts} to {to_ts}", file=sys.stderr)
        sys.exit(1)

    # STAGE 1: extract → print JSON facts to stdout, exit.
    if args.mode == "extract":
        facts = build_facts(rows, stats, hourly, from_ts, to_ts)
        print(json.dumps(facts, ensure_ascii=False, indent=2, default=str))
        return

    # STAGE 2: render → require --analysis
    if args.mode == "render":
        if not args.analysis:
            print("ERROR: --mode render requires --analysis <path>", file=sys.stderr)
            sys.exit(2)
        with open(args.analysis, "r", encoding="utf-8") as f:
            analysis = json.load(f)
        if "_source" not in analysis:
            analysis["_source"] = "agent"
    else:
        # AUTO fallback: build facts, run rule-based analysis
        facts = build_facts(rows, stats, hourly, from_ts, to_ts)
        analysis = fallback_analysis(facts)
        analysis["_source"] = "rule-based fallback"

    html = build_html(rows, stats, hourly, from_ts, to_ts, analysis)

    if args.output:
        out = args.output
    else:
        db_dir = os.path.dirname(os.path.abspath(args.db))
        dashboard_root = os.path.dirname(db_dir)
        reports_dir = os.path.join(dashboard_root, "public", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        prune_old_reports(reports_dir, keep=3)
        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
        out = os.path.join(reports_dir, f"report_{ts_str}.html")

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved: {out}")

if __name__ == "__main__":
    main()
