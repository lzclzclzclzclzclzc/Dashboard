#!/usr/bin/env python3
"""System Telemetry Report Generator
Queries the telemetry SQLite DB and generates a self-contained HTML report with Chinese anomaly analysis.
Usage:
  python generate_report.py --db <path> --range 24h --output report.html
  python generate_report.py --db <path> --range 7d
  python generate_report.py --db <path> --range custom --from "2026-06-20 00:00" --to "2026-06-22 00:00"
"""

import sqlite3, json, argparse, sys, os, statistics
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# ── Time range helpers ──────────────────────────────────────────────
def get_time_filter(args):
    if args.range == "custom":
        return args.from_ts, args.to_ts
    now = datetime.now()
    delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
             "7d": timedelta(days=7), "30d": timedelta(days=30)}
    return (now - delta[args.range]).strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")

def pick_label_format(duration_hours):
    if duration_hours <= 2:   return "%H:%M"
    if duration_hours <= 48:  return "%m/%d %H:%M"
    if duration_hours <= 720: return "%m/%d"
    return "%m/%d"

# ── DB queries ──────────────────────────────────────────────────────
def query_telemetry(db_path, from_ts, to_ts):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT ts, cpu_pct, cpu_temp, cpu_power, mem_pct, mem_used_gb, mem_total_gb,
               disk_free_gb, disk_total_gb, disk_pct, cpu_top10, mem_top10
        FROM telemetry WHERE ts >= ? AND ts < ? ORDER BY ts
    """, (from_ts, to_ts))
    rows = cur.fetchall()

    cur.execute("""
        SELECT
            COUNT(*) as samples,
            ROUND(MIN(cpu_pct),1) cpu_min, ROUND(AVG(cpu_pct),1) cpu_avg, ROUND(MAX(cpu_pct),1) cpu_max,
            ROUND(MIN(cpu_temp),1) temp_min, ROUND(AVG(cpu_temp),1) temp_avg, ROUND(MAX(cpu_temp),1) temp_max,
            ROUND(MIN(cpu_power),1) pwr_min, ROUND(AVG(cpu_power),1) pwr_avg, ROUND(MAX(cpu_power),1) pwr_max,
            ROUND(MIN(mem_pct),1) mem_min, ROUND(AVG(mem_pct),1) mem_avg, ROUND(MAX(mem_pct),1) mem_max,
            ROUND(AVG(mem_used_gb),1) mem_used, ROUND(AVG(mem_total_gb),1) mem_total,
            ROUND(AVG(disk_free_gb),1) disk_free, ROUND(AVG(disk_total_gb),1) disk_total,
            ROUND(MIN(disk_free_gb),1) disk_min, ROUND(MAX(disk_free_gb),1) disk_max
        FROM telemetry WHERE ts >= ? AND ts < ?
    """, (from_ts, to_ts))
    stats = dict(cur.fetchone())

    cur.execute("""
        SELECT strftime('%Y-%m-%d %H:00',ts) hour, COUNT(*) n,
               ROUND(AVG(cpu_pct),1) cpu, ROUND(AVG(cpu_temp),1) temp,
               ROUND(AVG(cpu_power),1) pwr, ROUND(AVG(mem_pct),1) mem
        FROM telemetry WHERE ts >= ? AND ts < ? GROUP BY hour ORDER BY hour
    """, (from_ts, to_ts))
    hourly = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, stats, hourly

# ── Process helpers ─────────────────────────────────────────────────
def parse_top10(row, field):
    try:
        return json.loads(row[field]) if row[field] else []
    except:
        return []

def aggregate_processes(rows, field, top_n=10):
    counter, mem_counter = Counter(), Counter()
    for row in rows:
        for p in parse_top10(row, field):
            name = p.get("Name", "Unknown")
            counter[name] = max(counter.get(name, 0), p.get("CPU", 0))
            mem_counter[name] = max(mem_counter.get(name, 0), p.get("MemMB", 0))
    return counter.most_common(top_n), mem_counter.most_common(top_n)

def process_snapshot(row):
    """返回该时刻的进程简要描述"""
    cpu_procs = parse_top10(row, "cpu_top10")
    if not cpu_procs:
        return "无进程数据"
    top = [f"{p.get('Name','?')}({p.get('CPU',0):.0f})" for p in cpu_procs[:3]]
    return "、".join(top)

# ── Anomaly Analysis Engine ─────────────────────────────────────────
def detect_anomalies(rows, stats):
    """多维度异常检测，返回带上下文的异常事件列表"""
    events = []
    if not rows:
        return events

    temp_vals = [r["cpu_temp"] for r in rows if r["cpu_temp"] is not None]
    cpu_vals  = [r["cpu_pct"] for r in rows if r["cpu_pct"] is not None]
    mem_vals  = [r["mem_pct"] for r in rows if r["mem_pct"] is not None]

    # 动态阈值：用中位数 + 1.5*IQR 或固定阈值中较严格的
    def pct(vals, p):
        if not vals: return 0
        s = sorted(vals)
        return s[int(len(s)*p/100)]

    temp_p90 = pct(temp_vals, 90)
    cpu_p90  = pct(cpu_vals, 90)
    mem_p90  = pct(mem_vals, 90)

    temp_median = pct(temp_vals, 50)
    cpu_median  = pct(cpu_vals, 50)

    temp_threshold = min(max(temp_p90, 85), 100)  # 至少85°C才算高温异常
    cpu_threshold  = min(max(cpu_p90, 30), 90)    # 至少30%才算CPU异常
    mem_threshold  = max(mem_p90, 70)              # 至少70%才算内存异常

    # 1. 温度尖峰 + 上下文
    for i, row in enumerate(rows):
        t = row["cpu_temp"]
        if t is None: continue
        if t >= temp_threshold:
            procs = process_snapshot(row)
            rel = ""
            if row["cpu_pct"] and row["cpu_pct"] < 15:
                rel = "但CPU负载极低，属于散热系统自身问题，非进程引起"
            elif row["cpu_pct"] and row["cpu_pct"] > 50:
                rel = "配合高CPU负载，温度飙升属正常应激"
            events.append({
                "ts": row["ts"], "type": "temp_spike", "severity": "high" if t >= 95 else "medium",
                "temp": t, "cpu_pct": row["cpu_pct"] or 0, "mem_pct": row["mem_pct"] or 0,
                "pwr": row["cpu_power"] or 0, "procs": procs, "note": rel
            })

    # 2. CPU 使用率暴增
    for i, row in enumerate(rows):
        c = row["cpu_pct"]
        if c is None: continue
        if c >= cpu_threshold:
            procs = process_snapshot(row)
            events.append({
                "ts": row["ts"], "type": "cpu_surge", "severity": "high" if c >= 80 else "medium",
                "temp": row["cpu_temp"] or 0, "cpu_pct": c, "mem_pct": row["mem_pct"] or 0,
                "pwr": row["cpu_power"] or 0, "procs": procs, "note": ""
            })

    # 3. 内存异常
    for i, row in enumerate(rows):
        m = row["mem_pct"]
        if m is None: continue
        if m >= mem_threshold:
            mem_procs = parse_top10(row, "mem_top10")
            top3 = "、".join([f"{p.get('Name','?')}({p.get('MemMB',0):.0f}MB)" for p in mem_procs[:3]])
            events.append({
                "ts": row["ts"], "type": "mem_pressure", "severity": "high" if m >= 85 else "medium",
                "temp": row["cpu_temp"] or 0, "cpu_pct": row["cpu_pct"] or 0, "mem_pct": m,
                "pwr": row["cpu_power"] or 0, "procs": ("内存大户: " + top3) if top3 else "无进程数据",
                "note": "内存紧张，可能触发压缩或换页"
            })

    # 4. 快速变化检测（相邻样本温差/CPU差过大）
    for i in range(1, len(rows)):
        t0, t1 = rows[i-1]["cpu_temp"], rows[i]["cpu_temp"]
        if t0 and t1 and abs(t1 - t0) >= 10:
            events.append({
                "ts": rows[i]["ts"], "type": "rapid_temp_change",
                "severity": "medium", "temp": t1, "cpu_pct": rows[i]["cpu_pct"] or 0,
                "mem_pct": rows[i]["mem_pct"] or 0, "pwr": rows[i]["cpu_power"] or 0,
                "procs": process_snapshot(rows[i]),
                "note": f"温度在1分钟内剧烈变化 {t0:.0f}→{t1:.0f}°C，可能风扇策略突变或传感器噪声"
            })

    # 去重：同类事件相邻时间合并（保留最严重的）
    events.sort(key=lambda e: (e["ts"], {"high": 0, "medium": 1}.get(e["severity"], 2)))
    merged = []
    skip = set()
    for i, ev in enumerate(events):
        if i in skip: continue
        # 找同类相邻事件
        cluster = [ev]
        for j in range(i+1, min(i+6, len(events))):
            if j in skip: continue
            if events[j]["type"] == ev["type"] and events[j]["severity"] == ev["severity"]:
                cluster.append(events[j])
                skip.add(j)
            else:
                break
        # 保留最严重的或第一个
        cluster.sort(key=lambda e: e.get("temp", 0) or e.get("cpu_pct", 0), reverse=True)
        merged.append(cluster[0])

    # 按严重度排序，每种类型最多保留前8条
    merged.sort(key=lambda e: (
        {"high": 0, "medium": 1}.get(e["severity"], 2),
        -(e.get("temp", 0) or e.get("cpu_pct", 0))
    ))
    type_counts = defaultdict(int)
    final = []
    for ev in merged:
        if type_counts[ev["type"]] < 8:
            final.append(ev)
            type_counts[ev["type"]] += 1

    return final

def write_analysis(anomalies):
    """根据异常事件生成中文分析报告"""
    if not anomalies:
        return '<p class="ok">✅ 未检测到明显异常，系统运行平稳。</p>'

    parts = []
    by_type = defaultdict(list)
    for a in anomalies:
        by_type[a["type"]].append(a)

    # 温度异常分析
    if by_type.get("temp_spike"):
        spikes = by_type["temp_spike"]
        highs = [s for s in spikes if s["severity"] == "high"]
        max_temp = max(s["temp"] for s in spikes)
        idle_hot = [s for s in spikes if s["cpu_pct"] < 20]
        busy_hot = [s for s in spikes if s["cpu_pct"] >= 20]

        text = f'<h3>🔥 温度异常（共 {len(spikes)} 次，最高 {max_temp:.0f}°C）</h3>'
        if idle_hot:
            text += f'<p>⚠️ <strong>关键问题：{len(idle_hot)} 次高温出现在 CPU 空闲时</strong>（CPU < 20%），说明散热系统存在问题——可能硅脂干涸、灰尘堵塞或风扇故障。空闲状态不应达到此温度。</p>'
        if busy_hot:
            text += f'<p>另有 {len(busy_hot)} 次高温出现在高负载场景，属正常范围但散热裕量不足。</p>'
        text += '<p><strong>建议：</strong>尽快清理风扇灰尘、更换导热硅脂。如果清灰后仍高温，需检查散热模组是否松动。</p>'

        # 列出具体事件
        text += '<ul>'
        for s in spikes[:6]:
            sev_badge = '<span class="badge danger">严重</span>' if s["severity"] == "high" else '<span class="badge warn">中等</span>'
            procs_info = f'<br><span class="muted">进程：{s["procs"]}</span>'
            extra = f'<br><span class="muted">{s["note"]}</span>' if s["note"] else ""
            text += f'<li>{sev_badge} {s["ts"]} — 温度 <strong>{s["temp"]:.0f}°C</strong>，CPU {s["cpu_pct"]:.0f}%，功耗 {s["pwr"]:.1f}W{procs_info}{extra}</li>'
        text += '</ul>'
        parts.append(text)

    # CPU 异常分析
    if by_type.get("cpu_surge"):
        surges = by_type["cpu_surge"]
        max_cpu = max(s["cpu_pct"] for s in surges)
        text = f'<h3>⚡ CPU 使用率暴增（共 {len(surges)} 次，最高 {max_cpu:.0f}%）</h3>'
        text += '<ul>'
        for s in surges[:6]:
            sev_badge = '<span class="badge danger">严重</span>' if s["severity"] == "high" else '<span class="badge warn">中等</span>'
            text += f'<li>{sev_badge} {s["ts"]} — CPU <strong>{s["cpu_pct"]:.0f}%</strong>，温度 {s["temp"]:.0f}°C，内存 {s["mem_pct"]:.0f}%<br><span class="muted">进程：{s["procs"]}</span></li>'
        text += '</ul>'
        parts.append(text)

    # 内存异常分析
    if by_type.get("mem_pressure"):
        presses = by_type["mem_pressure"]
        max_mem = max(s["mem_pct"] for s in presses)
        text = f'<h3>🧠 内存压力（共 {len(presses)} 次，最高 {max_mem:.0f}%）</h3>'
        text += '<ul>'
        for s in presses[:6]:
            sev_badge = '<span class="badge danger">严重</span>' if s["severity"] == "high" else '<span class="badge warn">中等</span>'
            text += f'<li>{sev_badge} {s["ts"]} — 内存 <strong>{s["mem_pct"]:.0f}%</strong>，CPU {s["cpu_pct"]:.0f}%<br><span class="muted">{s["procs"]}</span></li>'
        text += '</ul>'
        parts.append(text)

    # 温度快速变化
    if by_type.get("rapid_temp_change"):
        changes = by_type["rapid_temp_change"]
        text = f'<h3>📈 温度快速波动（共 {len(changes)} 次）</h3>'
        text += '<p>温度短时间内剧烈变化，可能的原因：风扇策略切换、传感器噪声或负载突变后散热系统响应延迟。</p>'
        text += '<ul>'
        for c in changes[:5]:
            text += f'<li>{c["ts"]} — {c["note"]}</li>'
        text += '</ul>'
        parts.append(text)

    return '\n'.join(parts)

# ── Overall summary analysis ────────────────────────────────────────
def overall_assessment(stats, anomalies, hourly):
    """生成整体评估段落"""
    temp_avg = stats.get("temp_avg", 0) or 0
    cpu_avg = stats.get("cpu_avg", 0) or 0
    mem_avg = stats.get("mem_avg", 0) or 0

    lines = ['<h2>📋 整体评估</h2>']

    # CPU温度评估
    if temp_avg >= 80:
        lines.append(f'<p>🔴 <strong>CPU 温度严重偏高：</strong>平均 {temp_avg:.0f}°C。即使 CPU 平均使用率仅 {cpu_avg:.0f}%，温度依然居高不下，散热系统明显异常。长时间维持这个温度会加速硬件老化。</p>')
    elif temp_avg >= 65:
        lines.append(f'<p>🟡 <strong>CPU 温度偏高：</strong>平均 {temp_avg:.0f}°C，对于空闲/轻载状态来说偏高。建议关注散热状况。</p>')
    else:
        lines.append(f'<p>🟢 CPU 温度正常，平均 {temp_avg:.0f}°C，散热表现良好。</p>')

    # CPU使用评估
    if cpu_avg >= 50:
        lines.append(f'<p>CPU 平均使用率 {cpu_avg:.0f}%，长时间高负载运行。主要贡献者见进程 Top 列表。</p>')
    elif cpu_avg >= 20:
        lines.append(f'<p>CPU 平均使用率 {cpu_avg:.0f}%，中等负载。</p>')
    else:
        lines.append(f'<p>CPU 平均使用率 {cpu_avg:.0f}%，负载较轻。</p>')

    # 内存评估
    if mem_avg >= 75:
        lines.append(f'<p>🔴 内存平均占用 {mem_avg:.0f}%，处于高位。建议关闭不用的程序或考虑增加内存。</p>')
    elif mem_avg >= 60:
        lines.append(f'<p>🟡 内存平均占用 {mem_avg:.0f}%，使用率偏高但尚可接受。</p>')
    else:
        lines.append(f'<p>🟢 内存平均占用 {mem_avg:.0f}%，充裕。</p>')

    # 异常汇总
    types = set(a["type"] for a in anomalies)
    type_descriptions = {
        "temp_spike": "温度尖峰",
        "cpu_surge": "CPU 使用率暴增",
        "mem_pressure": "内存压力",
        "rapid_temp_change": "温度快速波动"
    }
    type_names = [type_descriptions.get(t, t) for t in types]
    if type_names:
        sep = "、"
        lines.append(f'<p>检测到 <strong>{len(anomalies)}</strong> 个异常事件，涉及：{sep.join(type_names)}。</p>')

    # 休眠/无数据时段
    if hourly:
        gaps = []
        for i in range(1, len(hourly)):
            prev = datetime.strptime(hourly[i-1]["hour"], "%Y-%m-%d %H:%M")
            curr = datetime.strptime(hourly[i]["hour"], "%Y-%m-%d %H:%M")
            if (curr - prev).total_seconds() > 7200:
                gaps.append(f'{hourly[i-1]["hour"]} 至 {hourly[i]["hour"]}')
        if gaps:
            lines.append(f'<p class="muted">💤 休眠/关机时段：{"; ".join(gaps)}</p>')

    return '\n'.join(lines)

# ── HTML generation ─────────────────────────────────────────────────
def build_html(rows, stats, hourly, from_ts, to_ts, args):
    duration = (datetime.strptime(to_ts, "%Y-%m-%d %H:%M:%S") -
                datetime.strptime(from_ts, "%Y-%m-%d %H:%M:%S"))
    duration_hours = duration.total_seconds() / 3600

    def safe(v): return v if v is not None else "null"
    timestamps = [r["ts"] for r in rows]
    cpu_arr  = [safe(r["cpu_pct"]) for r in rows]
    temp_arr = [safe(r["cpu_temp"]) for r in rows]
    pwr_arr  = [safe(r["cpu_power"]) for r in rows]
    mem_arr  = [safe(r["mem_pct"]) for r in rows]
    disk_arr = [safe(r["disk_free_gb"]) for r in rows]

    cpu_procs, _   = aggregate_processes(rows, "cpu_top10")
    _, mem_consumers  = aggregate_processes(rows, "mem_top10")
    anomalies = detect_anomalies(rows, stats)
    analysis_html = write_analysis(anomalies)
    overview_html = overall_assessment(stats, anomalies, hourly)

    hourly_rows = ""
    for h in hourly:
        t = h["temp"] or 0
        color = "var(--danger)" if t >= 85 else ("var(--warn)" if t >= 75 else "var(--text)")
        hourly_rows += f"""<tr>
            <td>{h['hour']}</td><td>{h['n']}</td><td>{h['cpu']}%</td>
            <td style="color:{color};font-weight:600">{h['temp']}°C</td>
            <td>{h['pwr']}W</td><td>{h['mem']}%</td></tr>"""

    mem_total = stats.get("mem_total", 32)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>System Telemetry Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;--accent:#58a6ff;
  --danger:#f85149;--warn:#d2991d;--ok:#3fb950;--temp:#e06040;--cpu:#58a6ff;--pwr:#d2991d;--mem:#7c3aed;--disk:#3fb950}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;line-height:1.6}}
.header{{text-align:center;margin-bottom:32px}}
.header h1{{font-size:28px;color:var(--accent)}}
.header .sub{{color:var(--muted);margin-top:6px;font-size:14px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:32px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px}}
.card .label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.card .value{{font-size:28px;font-weight:700;margin:6px 0 2px}}
.card .sub{{font-size:12px;color:var(--muted)}}
.analysis{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:24px;margin-bottom:32px}}
.analysis h2{{color:var(--accent);margin-bottom:16px;font-size:20px}}
.analysis h3{{color:var(--warn);margin:20px 0 10px;font-size:16px}}
.analysis p{{margin:8px 0;font-size:14px}}
.analysis li{{margin:8px 0;font-size:13px;list-style:none;padding:8px 12px;background:var(--bg);border-radius:6px;border-left:3px solid var(--border)}}
.analysis .muted{{color:var(--muted);font-size:12px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:6px}}
.badge.danger{{background:var(--danger);color:#fff}}
.badge.warn{{background:var(--warn);color:#000}}
.chart-section{{margin-bottom:32px}}
.chart-section h2{{font-size:16px;color:var(--accent);margin-bottom:12px}}
.chart-wrap{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;position:relative;height:300px}}
.chart-wrap canvas{{width:100%!important;height:100%!important}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:900px){{.grid-2{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}}
th{{color:var(--muted);font-weight:600}}
.footer{{text-align:center;color:var(--muted);font-size:12px;margin-top:32px;padding-top:16px;border-top:1px solid var(--border)}}
.ok{{color:var(--ok)}}
</style></head>
<body>

<div class="header">
<h1>System Telemetry Report</h1>
<div class="sub">{from_ts} — {to_ts}（{duration_hours:.1f}小时，{stats['samples']} 条记录）</div>
</div>

<div class="cards">
<div class="card"><div class="label">CPU 使用率</div><div class="value" style="color:var(--cpu)">{stats['cpu_avg']}%</div><div class="sub">最低 {stats['cpu_min']}% / 最高 {stats['cpu_max']}%</div></div>
<div class="card"><div class="label">CPU 温度</div><div class="value" style="color:var(--temp)">{stats['temp_avg']}°C</div><div class="sub">最低 {stats['temp_min']}°C / 最高 {stats['temp_max']}°C</div></div>
<div class="card"><div class="label">CPU 功耗</div><div class="value" style="color:var(--pwr)">{stats['pwr_avg']}W</div><div class="sub">峰值 {stats['pwr_max']}W</div></div>
<div class="card"><div class="label">内存</div><div class="value" style="color:var(--mem)">{stats['mem_avg']}%</div><div class="sub">{stats['mem_used']}/{mem_total:.0f} GB</div></div>
<div class="card"><div class="label">磁盘可用</div><div class="value" style="color:var(--disk)">{stats['disk_free']} GB</div><div class="sub">共 {stats['disk_total']} GB</div></div>
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

<div class="analysis">
<h2>🔍 异常事件详细分析</h2>
{analysis_html}
</div>

<div class="grid-2">
<div class="chart-section"><h2>CPU 进程 Top 10</h2><table><tr><th>进程</th><th>最高 CPU</th></tr>
{''.join(f'<tr><td>{n}</td><td>{v:.0f}</td></tr>' for n,v in cpu_procs[:10])}</table></div>
<div class="chart-section"><h2>内存进程 Top 10</h2><table><tr><th>进程</th><th>最高内存</th></tr>
{''.join(f'<tr><td>{n}</td><td>{v:.0f} MB</td></tr>' for n,v in mem_consumers[:10])}</table></div>
</div>

<div class="chart-section"><h2>逐小时汇总</h2><table>
<tr><th>时段</th><th>样本</th><th>CPU%</th><th>温度</th><th>功耗</th><th>内存%</th></tr>{hourly_rows}</table></div>

<div class="footer">由 telemetry-report skill 生成 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

<script>
const ts={json.dumps(timestamps)};
const o=(c,u,m)=>({{responsive:!0,maintainAspectRatio:!1,plugins:{{legend:{{display:!1}}}},scales:{{x:{{ticks:{{color:'#8b949e',maxTicksLimit:20,autoSkip:!0}}}},y:{{ticks:{{color:'#8b949e'}},max:m,grid:{{color:'#21262d'}}}}}},elements:{{point:{{radius:0}},line:{{borderWidth:1.5,borderColor:c}}}}}});
new Chart(document.getElementById('chartCpu'),{{type:'line',data:{{labels:ts,datasets:[{{data:{cpu_arr},borderColor:'#58a6ff'}}]}},options:o('#58a6ff','%',100)}});
new Chart(document.getElementById('chartTemp'),{{type:'line',data:{{labels:ts,datasets:[{{data:{temp_arr},borderColor:'#e06040'}}]}},options:o('#e06040','°C')}});
new Chart(document.getElementById('chartPower'),{{type:'line',data:{{labels:ts,datasets:[{{data:{pwr_arr},borderColor:'#d2991d'}}]}},options:o('#d2991d','W')}});
new Chart(document.getElementById('chartMem'),{{type:'line',data:{{labels:ts,datasets:[{{data:{mem_arr},borderColor:'#7c3aed'}}]}},options:o('#7c3aed','%',100)}});
new Chart(document.getElementById('chartDisk'),{{type:'line',data:{{labels:ts,datasets:[{{data:{disk_arr},borderColor:'#3fb950'}}]}},options:o('#3fb950','GB')}});
</script>
</body></html>"""
    return html

def prune_old_reports(reports_dir, keep=3):
    """Keep only the latest N reports."""
    if not os.path.isdir(reports_dir):
        return
    files = sorted(
        [f for f in os.listdir(reports_dir) if f.endswith(".html")],
        key=lambda f: os.path.getmtime(os.path.join(reports_dir, f)),
        reverse=True
    )
    for old in files[keep:]:
        os.remove(os.path.join(reports_dir, old))
        print(f"Pruned old report: {old}")

# ── Main ────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Generate telemetry HTML report with Chinese analysis")
    p.add_argument("--db", required=True, help="Path to telemetry.db")
    p.add_argument("--range", choices=["1h","24h","7d","30d","custom"], default="24h")
    p.add_argument("--from", dest="from_ts", help="Custom start (YYYY-MM-DD HH:MM)")
    p.add_argument("--to", dest="to_ts", help="Custom end (YYYY-MM-DD HH:MM)")
    p.add_argument("--output", "-o", default=None, help="Output HTML path (auto-detects dashboard dir if omitted)")
    args = p.parse_args()

    from_ts, to_ts = get_time_filter(args)
    rows, stats, hourly = query_telemetry(args.db, from_ts, to_ts)

    if not rows:
        print(f"No data found in range {from_ts} to {to_ts}")
        sys.exit(1)

    html = build_html(rows, stats, hourly, from_ts, to_ts, args)

    if args.output:
        out = args.output
    else:
        # Auto-detect dashboard reports directory
        db_dir = os.path.dirname(os.path.abspath(args.db))
        dashboard_root = os.path.dirname(db_dir)
        reports_dir = os.path.join(dashboard_root, "public", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        out = os.path.join(reports_dir, f"report_{ts}.html")

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved: {out}")

if __name__ == "__main__":
    main()
