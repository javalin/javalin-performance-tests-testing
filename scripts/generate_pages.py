#!/usr/bin/env python3
import argparse
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class Record:
    run_id: str
    run_timestamp: str
    version: str
    benchmark: str
    score: float
    score_error: Optional[float]
    score_unit: str
    threads: Optional[int]
    forks: Optional[int]
    measurement_iterations: Optional[int]
    measurement_time: Optional[str]


def read_json(path: Path):
    return json.loads(path.read_text())


def parse_jmh_result(path: Path, run_id: str, run_timestamp: str, version: str) -> List[Record]:
    data = read_json(path)
    if not isinstance(data, list):
        return []

    records: List[Record] = []
    for item in data:
        try:
            metric = item.get("primaryMetric", {})
            score = float(metric["score"])
            score_error_raw = metric.get("scoreError")
            score_error = float(score_error_raw) if score_error_raw not in (None, "NaN") else None
            records.append(
                Record(
                    run_id=run_id,
                    run_timestamp=run_timestamp,
                    version=version,
                    benchmark=item.get("benchmark", "<unknown>"),
                    score=score,
                    score_error=score_error,
                    score_unit=metric.get("scoreUnit", ""),
                    threads=item.get("threads"),
                    forks=item.get("forks"),
                    measurement_iterations=item.get("measurementIterations"),
                    measurement_time=item.get("measurementTime"),
                )
            )
        except Exception:
            continue
    return records


def safe_float(value: Optional[float], digits: int = 4) -> str:
    if value is None:
        return ""
    if math.isnan(value) or math.isinf(value):
        return ""
    return f"{value:.{digits}f}"


def sort_key(record: Record):
    return record.run_timestamp, record.run_id


def load_records(history_root: Path) -> Tuple[List[Record], Dict[str, dict], Dict[str, dict]]:
    records: List[Record] = []
    run_metadata: Dict[str, dict] = {}
    run_runner: Dict[str, dict] = {}

    if not history_root.exists():
        return records, run_metadata, run_runner

    for run_dir in sorted([path for path in history_root.iterdir() if path.is_dir()]):
        run_id = run_dir.name
        metadata_path = run_dir / "run-metadata.json"
        runner_path = run_dir / "runner-info.json"

        metadata = read_json(metadata_path) if metadata_path.exists() else {}
        runner = read_json(runner_path) if runner_path.exists() else {}
        run_metadata[run_id] = metadata
        run_runner[run_id] = runner

        run_timestamp = metadata.get("runTimestampUtc", run_id)
        results_dir = run_dir / "results"
        if not results_dir.exists():
            continue

        for result_file in sorted(results_dir.glob("*.json")):
            version = result_file.stem
            records.extend(parse_jmh_result(result_file, run_id, run_timestamp, version))

    return records, run_metadata, run_runner


def summarize(records: Iterable[Record]):
    grouped: Dict[Tuple[str, str], List[Record]] = {}
    for record in records:
        grouped.setdefault((record.version, record.benchmark), []).append(record)

    summary_rows = []
    for _, values in grouped.items():
        values.sort(key=sort_key)
        latest = values[-1]
        previous = values[-2] if len(values) > 1 else None
        delta_percent = None
        if previous and previous.score != 0:
            delta_percent = ((latest.score / previous.score) - 1.0) * 100.0

        sample = [value.score for value in values[-8:]]
        mean_score = statistics.fmean(sample)
        stdev_score = statistics.stdev(sample) if len(sample) >= 2 else None
        cv_percent = (stdev_score / mean_score * 100.0) if stdev_score is not None and mean_score else None

        summary_rows.append(
            {
                "version": latest.version,
                "benchmark": latest.benchmark,
                "latestRun": latest.run_id,
                "latestTimestamp": latest.run_timestamp,
                "latestScore": latest.score,
                "latestScoreError": latest.score_error,
                "scoreUnit": latest.score_unit,
                "deltaVsPreviousPercent": delta_percent,
                "meanLast8": mean_score,
                "stdevLast8": stdev_score,
                "cvLast8Percent": cv_percent,
                "samples": len(values),
                "threads": latest.threads,
                "forks": latest.forks,
                "measurementIterations": latest.measurement_iterations,
                "measurementTime": latest.measurement_time,
                "history": [
                    {
                        "runId": value.run_id,
                        "runTimestamp": value.run_timestamp,
                        "score": value.score,
                        "scoreError": value.score_error,
                    }
                    for value in values
                ],
            }
        )

    summary_rows.sort(key=lambda row: (row["benchmark"], row["version"]))

    benchmark_max: Dict[str, float] = {}
    for row in summary_rows:
        current = benchmark_max.get(row["benchmark"])
        if current is None or row["latestScore"] > current:
            benchmark_max[row["benchmark"]] = row["latestScore"]

    for row in summary_rows:
        row["isWinner"] = row["latestScore"] == benchmark_max.get(row["benchmark"])

    return summary_rows


def compact_timestamp(value: str) -> str:
    if len(value) >= 16 and "T" in value:
        return value[5:16].replace("T", " ")
    return value


def compact_sidebar_timestamp(value: str) -> str:
    if len(value) >= 10:
        return value[:10]
    return value


def render_line_chart_svg(benchmark: str, score_unit: str, run_labels: List[str], series: List[dict]) -> str:
    width = 980
    height = 280
    left = 60
    right = 20
    top = 20
    bottom = 45
    plot_width = width - left - right
    plot_height = height - top - bottom

    values = [value for line in series for value in line["values"] if value is not None]
    if not values:
        return f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{escape(benchmark)} trend chart'><text x='20' y='30'>No data</text></svg>"

    y_min = min(values)
    y_max = max(values)
    if y_max == y_min:
        y_min -= 1.0
        y_max += 1.0
    else:
        padding = (y_max - y_min) * 0.12
        y_min -= padding
        y_max += padding

    run_count = len(run_labels)

    def x_of(index: int) -> float:
        if run_count <= 1:
            return left + plot_width / 2
        return left + (plot_width * index / (run_count - 1))

    def y_of(value: float) -> float:
        return top + (y_max - value) * plot_height / (y_max - y_min)

    grid_lines = []
    tick_count = 5
    for index in range(tick_count):
        ratio = index / (tick_count - 1)
        tick_value = y_max - (y_max - y_min) * ratio
        y = top + ratio * plot_height
        grid_lines.append(f"<line x1='{left}' y1='{y:.2f}' x2='{left + plot_width}' y2='{y:.2f}' stroke='#dce9f6' stroke-width='1' />")
        grid_lines.append(f"<text x='{left - 8}' y='{y + 4:.2f}' text-anchor='end' fill='#50667f' font-size='11'>{tick_value:.2f}</text>")

    x_labels = []
    if run_count > 0:
        x_labels.append((0, run_labels[0]))
    if run_count > 2:
        x_labels.append((run_count // 2, run_labels[run_count // 2]))
    if run_count > 1:
        x_labels.append((run_count - 1, run_labels[-1]))

    x_tick_labels = []
    seen = set()
    for index, label in x_labels:
        if index in seen:
            continue
        seen.add(index)
        x = x_of(index)
        x_tick_labels.append(f"<text x='{x:.2f}' y='{height - 12}' text-anchor='middle' fill='#50667f' font-size='11'>{escape(label)}</text>")

    line_shapes = []
    legend_items = []
    for legend_index, line in enumerate(series):
        points = []
        for value_index, value in enumerate(line["values"]):
            if value is None:
                continue
            points.append((x_of(value_index), y_of(value)))

        if len(points) >= 2:
            polyline_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            line_shapes.append(
                f"<polyline fill='none' stroke='{line['color']}' stroke-width='2.4' points='{polyline_points}' />"
            )

        for x, y in points:
            line_shapes.append(f"<circle cx='{x:.2f}' cy='{y:.2f}' r='2.8' fill='{line['color']}' />")

        legend_x = left + 8 + (legend_index * 160)
        legend_y = top + 10
        legend_items.append(f"<line x1='{legend_x}' y1='{legend_y}' x2='{legend_x + 16}' y2='{legend_y}' stroke='{line['color']}' stroke-width='3' />")
        legend_items.append(
            f"<text x='{legend_x + 22}' y='{legend_y + 4}' fill='#21344d' font-size='12'>{escape(line['label'])}</text>"
        )

    return (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{escape(benchmark)} trend chart'>"
        f"<rect x='0' y='0' width='{width}' height='{height}' fill='white' rx='10' />"
        f"<text x='{left}' y='16' fill='#132236' font-size='13'>{escape(benchmark)} ({escape(score_unit)})</text>"
        + "".join(grid_lines)
        + f"<line x1='{left}' y1='{top}' x2='{left}' y2='{top + plot_height}' stroke='#9fb6cc' stroke-width='1.2' />"
        + f"<line x1='{left}' y1='{top + plot_height}' x2='{left + plot_width}' y2='{top + plot_height}' stroke='#9fb6cc' stroke-width='1.2' />"
        + "".join(line_shapes)
        + "".join(legend_items)
        + "".join(x_tick_labels)
        + "</svg>"
    )


def build_chart_blocks(records: List[Record]) -> str:
    grouped: Dict[str, List[Record]] = {}
    for record in records:
        grouped.setdefault(record.benchmark, []).append(record)

    colors = ["#0f8b8d", "#c23b4f", "#2a63d4", "#ef8354", "#1f7a8c", "#4f5d75"]
    blocks = []

    for benchmark in sorted(grouped.keys()):
        values = grouped[benchmark]
        values.sort(key=sort_key)

        unique_runs = sorted({(record.run_id, record.run_timestamp) for record in values}, key=lambda item: (item[1], item[0]))
        run_index = {run_id: idx for idx, (run_id, _) in enumerate(unique_runs)}
        run_labels = [compact_timestamp(run_timestamp) for _, run_timestamp in unique_runs]

        versions = sorted({record.version for record in values})
        score_unit = values[0].score_unit if values else ""
        series = []

        for version_index, version in enumerate(versions):
            line_values: List[Optional[float]] = [None] * len(unique_runs)
            for record in [item for item in values if item.version == version]:
                line_values[run_index[record.run_id]] = record.score
            series.append(
                {
                    "label": version,
                    "values": line_values,
                    "color": colors[version_index % len(colors)],
                }
            )

        svg = render_line_chart_svg(benchmark, score_unit, run_labels, series)
        blocks.append(
            "<section class='chart-card'>"
            + f"<h3>{escape(benchmark)}</h3>"
            + svg
            + "</section>"
        )

    if not blocks:
        return "<p>No trend data yet.</p>"

    return "\n".join(blocks)


def select_latest_run_id(run_metadata: Dict[str, dict]) -> Optional[str]:
    if not run_metadata:
        return None
    return max(
        run_metadata.keys(),
        key=lambda run_id: (
            run_metadata.get(run_id, {}).get("runTimestampUtc", ""),
            run_id,
        ),
    )


def sorted_run_ids(run_metadata: Dict[str, dict]) -> List[str]:
    return sorted(
        run_metadata.keys(),
        key=lambda run_id: (run_metadata.get(run_id, {}).get("runTimestampUtc", ""), run_id),
    )


def summarize_table_rows(rows: List[dict]) -> str:
    if not rows:
        return "<tr><td colspan='15'>No data yet.</td></tr>"

    rendered = []
    for row in rows:
        winner_badge = "<span class='winner-badge'>Best</span>" if row.get("isWinner") else ""
        row_class = "winner-row" if row.get("isWinner") else ""
        rendered.append(
            "<tr class='" + row_class + "'>"
            + f"<td>{escape(row['benchmark'])}</td>"
            + f"<td>{escape(row['version'])}</td>"
            + f"<td>{winner_badge}</td>"
            + f"<td>{safe_float(row['latestScore'])}</td>"
            + f"<td>{safe_float(row['latestScoreError'])}</td>"
            + f"<td>{escape(row['scoreUnit'])}</td>"
            + f"<td>{safe_float(row['deltaVsPreviousPercent'], 2)}</td>"
            + f"<td>{safe_float(row['meanLast8'])}</td>"
            + f"<td>{safe_float(row['stdevLast8'])}</td>"
            + f"<td>{safe_float(row['cvLast8Percent'], 2)}</td>"
            + f"<td>{row['samples']}</td>"
            + f"<td>{row['threads'] if row['threads'] is not None else ''}</td>"
            + f"<td>{row['forks'] if row['forks'] is not None else ''}</td>"
            + f"<td>{row['measurementIterations'] if row['measurementIterations'] is not None else ''}</td>"
            + f"<td>{escape(row['measurementTime'] or '')}</td>"
            + "</tr>"
        )
    return "\n".join(rendered)


def render_sidebar(
    run_timeline: List[dict],
    active_run_id: Optional[str],
    latest_run_id: Optional[str],
    root_rel: str,
) -> str:
    links = [
        (
            f"{root_rel}/index.html",
            "Latest (full history)",
            latest_run_id or "",
            active_run_id is None,
        )
    ]

    for item in reversed(run_timeline):
        run_id = item["runId"]
        run_ts = compact_sidebar_timestamp(item["runTimestampUtc"])
        links.append(
            (
                f"{root_rel}/runs/{run_id}.html",
                run_id,
                run_ts,
                active_run_id == run_id,
            )
        )

    rendered_links = "\n".join(
        "<li>"
        + f"<a class='{'active' if is_active else ''}' href='{escape(href)}'>"
        + f"<span class='run-title'>{escape(title)}</span>"
        + (f"<span class='run-sub'>{escape(sub)}</span>" if sub else "")
        + "</a>"
        + "</li>"
        for href, title, sub, is_active in links
    )

    return (
        "<aside class='sidebar'>"
        "<div class='sidebar-card'>"
        "<h2>Reports</h2>"
        "<p>Navigate weekly snapshots. Each snapshot includes history up to that week.</p>"
        "<ul class='run-list'>"
        + rendered_links
        + "</ul>"
        "</div>"
        "</aside>"
    )


def build_html(
    repo: str,
    rows: List[dict],
    charts_html: str,
    latest_run_id: Optional[str],
    latest_meta: dict,
    latest_runner: dict,
    run_timeline: List[dict],
    active_run_id: Optional[str],
    root_rel: str,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    benchmark_settings = latest_meta.get("benchmarkSettings", {}) if latest_meta else {}
    versions_setting = benchmark_settings.get("versions", [])
    if isinstance(versions_setting, list):
        versions_list = [str(item) for item in versions_setting]
    elif versions_setting:
        versions_list = [str(versions_setting)]
    else:
        versions_list = []
    versions_preview = ", ".join(versions_list[:8]) if versions_list else ""
    if len(versions_list) > 8:
        versions_preview = f"{versions_preview}, ... , {versions_list[-1]}"
    versions_count = len(versions_list)

    env = latest_runner.get("environment", {}) if latest_runner else {}
    cpu = latest_runner.get("cpu", {}) if latest_runner else {}
    cpu_details = cpu.get("details", {}) if isinstance(cpu.get("details"), dict) else {}
    mem = latest_runner.get("memory", {}) if latest_runner else {}

    table_rows = summarize_table_rows(rows)
    sidebar = render_sidebar(run_timeline, active_run_id, latest_run_id, root_rel)

    mode_title = "Latest cumulative report" if active_run_id is None else f"Snapshot for {active_run_id}"

    explain_html = """
      <section class='help-card'>
        <h2>How To Read This</h2>
        <p>Pretend each benchmark is a race. The fastest racer wins.</p>
        <p><strong>Higher score is better.</strong> Score is <code>ops/ms</code>: how many requests finished in one millisecond.</p>
        <p><strong>Benchmark Settings</strong> show what this specific run actually executed.</p>
        <p>In the summary table, <strong>Best</strong> marks the winner for that benchmark in this report.</p>
        <p><strong>Delta vs Prev %</strong> compares this run to the previous run for the same version and benchmark.</p>
        <p><strong>CV%</strong> is consistency: lower means more stable numbers over time.</p>
      </section>
    """

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{escape(repo)} benchmark report</title>
  <style>
    :root {{
      --bg: #e4eef7;
      --panel: #ffffff;
      --ink: #122235;
      --muted: #4e657f;
      --line: #d2e1f0;
      --accent: #1f7a8c;
      --winner-bg: #eaf7ef;
      --winner-border: #2c7a53;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: 'IBM Plex Sans', 'Segoe UI', sans-serif;
      background: radial-gradient(circle at 0% 0%, #cfe4f7 0%, var(--bg) 42%);
    }}
    .layout {{
      max-width: 1600px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 270px minmax(0, 1fr);
      gap: 1rem;
      padding: 1rem;
    }}
    .sidebar {{ position: sticky; top: .8rem; height: fit-content; }}
    .sidebar-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: .9rem;
    }}
    .sidebar-card h2 {{ margin: 0 0 .35rem; font-size: 1rem; }}
    .sidebar-card p {{ margin: 0 0 .75rem; color: var(--muted); font-size: .85rem; line-height: 1.35; }}
    .run-list {{ list-style: none; margin: 0; padding: 0; display: grid; gap: .4rem; max-height: 80vh; overflow: auto; }}
    .run-list a {{
      display: block;
      text-decoration: none;
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: .5rem .55rem;
      background: #f9fcff;
    }}
    .run-list a.active {{ border-color: var(--accent); background: #e8f4fb; }}
    .run-title {{ display: block; font-size: .84rem; font-weight: 600; }}
    .run-sub {{ display: block; font-size: .72rem; color: var(--muted); margin-top: .15rem; }}
    .content {{ min-width: 0; }}
    .header {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1rem;
      margin-bottom: .95rem;
    }}
    h1 {{ margin: 0 0 .35rem; font-size: 1.75rem; }}
    .meta {{ color: var(--muted); font-size: .92rem; margin-bottom: .2rem; }}
    .meta strong {{ color: var(--ink); }}
    .cards {{
      display: grid;
      gap: .7rem;
      grid-template-columns: repeat(4, minmax(170px, 1fr));
      margin-top: .8rem;
    }}
    .card {{
      background: #f7fbff;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: .7rem;
      min-width: 0;
    }}
    .label {{ color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; margin-bottom: .2rem; }}
    .value {{ font-family: 'IBM Plex Mono', 'Consolas', monospace; font-size: .84rem; line-height: 1.38; word-break: break-word; }}
    .help-card {{
      margin-top: .75rem;
      background: #f5fbff;
      border: 1px solid #c8dfef;
      border-radius: 10px;
      padding: .85rem .95rem;
    }}
    .help-card h2 {{ margin: 0 0 .45rem; font-size: 1.05rem; }}
    .help-card p {{ margin: .25rem 0; line-height: 1.4; }}
    .help-card code {{ background: #e7f2fb; border-radius: 4px; padding: 0 .25rem; }}
    h2 {{ margin: 1rem 0 .5rem; font-size: 1.2rem; }}
    h3 {{ margin: 0 0 .45rem; font-size: .95rem; }}
    .summary-wrap {{
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: auto;
      max-height: 64vh;
      background: var(--panel);
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: .87rem; }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: .45rem .4rem;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}
    th {{ position: sticky; top: 0; background: #e9f3fb; z-index: 1; }}
    .winner-row {{ background: var(--winner-bg); }}
    .winner-row td:first-child {{ border-left: 4px solid var(--winner-border); }}
    .winner-badge {{
      display: inline-block;
      border: 1px solid var(--winner-border);
      border-radius: 999px;
      padding: .08rem .44rem;
      font-size: .72rem;
      font-weight: 700;
      background: #dff1e8;
      color: #184d33;
    }}
    .chart-grid {{ display: grid; grid-template-columns: 1fr; gap: .8rem; }}
    .chart-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: .75rem;
      overflow-x: auto;
    }}
    footer {{ margin-top: .85rem; color: var(--muted); font-size: .84rem; }}
    @media (max-width: 1150px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      .run-list {{ max-height: none; }}
      .cards {{ grid-template-columns: repeat(2, minmax(170px, 1fr)); }}
    }}
    @media (max-width: 680px) {{
      .cards {{ grid-template-columns: 1fr; }}
      th, td {{ font-size: .8rem; }}
      h1 {{ font-size: 1.35rem; }}
    }}
  </style>
</head>
<body>
  <div class=\"layout\">
    {sidebar}
    <main class=\"content\">
      <section class=\"header\">
        <h1>Javalin Performance Benchmarks</h1>
        <div class=\"meta\">Repository: <strong>{escape(repo)}</strong></div>
        <div class=\"meta\">View: <strong>{escape(mode_title)}</strong> | Generated: {escape(generated)} | Latest run in history: {escape(latest_run_id or '')}</div>

        <div class=\"cards\">
          <div class=\"card\">
            <div class=\"label\">Benchmark Settings</div>
            <div class=\"value\">versionCount={versions_count}<br>versions={escape(versions_preview)}<br>iterations={escape(str(benchmark_settings.get('iterations', '')))}<br>iterationTimeMs={escape(str(benchmark_settings.get('iterationTimeMs', '')))}<br>forks={escape(str(benchmark_settings.get('forks', '')))}<br>threads={escape(str(benchmark_settings.get('threads', '')))}</div>
          </div>
          <div class=\"card\">
            <div class=\"label\">Runner Image</div>
            <div class=\"value\">ImageOS={escape(env.get('ImageOS', ''))}<br>ImageVersion={escape(env.get('ImageVersion', ''))}<br>Runner={escape(env.get('RUNNER_NAME', ''))}<br>OS={escape(env.get('RUNNER_OS', ''))}/{escape(env.get('RUNNER_ARCH', ''))}</div>
          </div>
          <div class=\"card\">
            <div class=\"label\">CPU</div>
            <div class=\"value\">model={escape(cpu_details.get('Model name', ''))}<br>nproc={escape(str(cpu.get('nproc', '')))}<br>cores={escape(cpu_details.get('CPU(s)', ''))}<br>maxMHz={escape(cpu_details.get('CPU max MHz', ''))}</div>
          </div>
          <div class=\"card\">
            <div class=\"label\">Memory</div>
            <div class=\"value\">memTotal={escape(mem.get('meminfo', {}).get('MemTotal', ''))}<br>swapTotal={escape(mem.get('meminfo', {}).get('SwapTotal', ''))}<br>cgroupCpuMax={escape(str(cpu.get('cgroupCpuMax', '')))}</div>
          </div>
        </div>

        {explain_html}
      </section>

      <h2>Latest Summary</h2>
      <div class=\"summary-wrap\">
        <table>
          <thead>
            <tr>
              <th>Benchmark</th>
              <th>Version</th>
              <th>Winner</th>
              <th>Latest Score</th>
              <th>Score Error</th>
              <th>Unit</th>
              <th>Delta vs Prev %</th>
              <th>Mean (last 8)</th>
              <th>Stdev (last 8)</th>
              <th>CV% (last 8)</th>
              <th>Samples</th>
              <th>Threads</th>
              <th>Forks</th>
              <th>Meas. Iter.</th>
              <th>Meas. Time</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </div>

      <h2>Trend Charts</h2>
      <div class=\"chart-grid\">
        {charts_html}
      </div>

      <footer>Higher score is better in throughput mode. Compare versions within the same benchmark row. CV% is stdev/mean over up to the last 8 samples.</footer>
    </main>
  </div>
</body>
</html>
"""


def make_run_timeline(run_metadata: Dict[str, dict]) -> List[dict]:
    timeline = []
    for run_id in sorted_run_ids(run_metadata):
        timeline.append(
            {
                "runId": run_id,
                "runTimestampUtc": run_metadata.get(run_id, {}).get("runTimestampUtc", run_id),
                "page": f"runs/{run_id}.html",
            }
        )
    return timeline


def records_upto_run(records: List[Record], run_order: List[str], run_id: str) -> List[Record]:
    if run_id not in run_order:
        return []
    idx = run_order.index(run_id)
    included = set(run_order[: idx + 1])
    return [record for record in records if record.run_id in included]


def write_report(
    output_path: Path,
    repo: str,
    rows: List[dict],
    records: List[Record],
    latest_run_id: Optional[str],
    latest_meta: dict,
    latest_runner: dict,
    run_timeline: List[dict],
    active_run_id: Optional[str],
    root_rel: str,
) -> None:
    charts_html = build_chart_blocks(records)
    output_path.write_text(
        build_html(
            repo=repo,
            rows=rows,
            charts_html=charts_html,
            latest_run_id=latest_run_id,
            latest_meta=latest_meta,
            latest_runner=latest_runner,
            run_timeline=run_timeline,
            active_run_id=active_run_id,
            root_rel=root_rel,
        )
    )


def main():
    parser = argparse.ArgumentParser(description="Generate static benchmark report pages")
    parser.add_argument("--history-root", required=True, help="Directory containing runs/<run-id>")
    parser.add_argument("--output-dir", required=True, help="Output directory for static pages")
    parser.add_argument("--repository", required=True, help="Repository name for display")
    args = parser.parse_args()

    history_root = Path(args.history_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "runs").mkdir(parents=True, exist_ok=True)

    records, run_metadata, run_runner = load_records(history_root)
    latest_run_id = select_latest_run_id(run_metadata)

    latest_meta = run_metadata.get(latest_run_id, {}) if latest_run_id else {}
    latest_runner = run_runner.get(latest_run_id, {}) if latest_run_id else {}

    run_timeline = make_run_timeline(run_metadata)
    run_order = [item["runId"] for item in run_timeline]

    summary_rows = summarize(records)

    summary_payload = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "repository": args.repository,
        "totalRecords": len(records),
        "totalBenchmarks": len(summary_rows),
        "latestRunId": latest_run_id,
        "runIndex": run_timeline,
        "rows": summary_rows,
    }

    (output_dir / "summary.json").write_text(json.dumps(summary_payload, indent=2) + "\n")

    write_report(
        output_path=output_dir / "index.html",
        repo=args.repository,
        rows=summary_rows,
        records=records,
        latest_run_id=latest_run_id,
        latest_meta=latest_meta,
        latest_runner=latest_runner,
        run_timeline=run_timeline,
        active_run_id=None,
        root_rel=".",
    )

    for run_id in run_order:
        snapshot_records = records_upto_run(records, run_order, run_id)
        snapshot_rows = summarize(snapshot_records)
        snapshot_meta = run_metadata.get(run_id, {})
        snapshot_runner = run_runner.get(run_id, {})
        write_report(
            output_path=output_dir / "runs" / f"{run_id}.html",
            repo=args.repository,
            rows=snapshot_rows,
            records=snapshot_records,
            latest_run_id=latest_run_id,
            latest_meta=snapshot_meta,
            latest_runner=snapshot_runner,
            run_timeline=run_timeline,
            active_run_id=run_id,
            root_rel="..",
        )


if __name__ == "__main__":
    main()
