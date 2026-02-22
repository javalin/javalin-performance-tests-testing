#!/usr/bin/env python3
import argparse
import json
import math
import re
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

    benchmark_groups: Dict[str, List[dict]] = {}
    for row in summary_rows:
        benchmark_groups.setdefault(row["benchmark"], []).append(row)

    def relative_error_percent(score: float, score_error: Optional[float]) -> Optional[float]:
        if score_error is None or score == 0:
            return None
        return abs(score_error / score) * 100.0

    def uncertainty_percent(row: dict) -> float:
        candidates = [2.0]  # minimum uncertainty floor
        cv = row.get("cvLast8Percent")
        rel_err = relative_error_percent(row.get("latestScore", 0.0), row.get("latestScoreError"))
        if cv is not None and math.isfinite(cv):
            candidates.append(abs(cv))
        if rel_err is not None and math.isfinite(rel_err):
            candidates.append(abs(rel_err))
        return min(max(candidates), 20.0)

    for benchmark_rows in benchmark_groups.values():
        best_row = max(benchmark_rows, key=lambda item: item["latestScore"])
        best_score = best_row["latestScore"]
        best_uncertainty = uncertainty_percent(best_row)

        for row in benchmark_rows:
            row_uncertainty = uncertainty_percent(row)
            combined_band = min(max(math.sqrt(best_uncertainty**2 + row_uncertainty**2), 2.0), 20.0)
            delta_from_best = 0.0
            if best_score != 0:
                delta_from_best = ((best_score - row["latestScore"]) / best_score) * 100.0

            row["strictBest"] = row is best_row
            row["bestBandPercent"] = combined_band
            row["deltaFromBestPercent"] = delta_from_best
            row["coBest"] = delta_from_best <= combined_band + 1e-9
            row["uncertaintyPercent"] = row_uncertainty

    return summary_rows


def compact_timestamp(value: str) -> str:
    if len(value) >= 16 and "T" in value:
        return value[5:16].replace("T", " ")
    return value


def compact_sidebar_timestamp(value: str) -> str:
    if len(value) >= 10:
        return value[:10]
    return value


def display_timestamp(value: str) -> str:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return value


def build_chart_data_map(records: List[Record]) -> Dict[str, dict]:
    grouped: Dict[str, List[Record]] = {}
    for record in records:
        grouped.setdefault(record.benchmark, []).append(record)

    colors = ["#0f8b8d", "#c23b4f", "#2a63d4", "#ef8354", "#1f7a8c", "#4f5d75"]
    chart_data: Dict[str, dict] = {}

    for benchmark in sorted(grouped.keys()):
        values = grouped[benchmark]
        values.sort(key=sort_key)

        unique_runs = sorted({(record.run_id, record.run_timestamp) for record in values}, key=lambda item: (item[1], item[0]))
        run_index = {run_id: idx for idx, (run_id, _) in enumerate(unique_runs)}

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

        chart_data[benchmark] = {
            "benchmark": benchmark,
            "scoreUnit": score_unit,
            "runs": [
                {
                    "runId": run_id,
                    "label": compact_timestamp(run_timestamp),
                    "fullLabel": display_timestamp(run_timestamp),
                }
                for run_id, run_timestamp in unique_runs
            ],
            "series": [
                {
                    "name": item["label"],
                    "data": item["values"],
                    "color": item["color"],
                }
                for item in series
            ],
        }

    return chart_data


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


def benchmark_group_label(benchmark: str) -> str:
    short = benchmark.split(".")[-1]
    mapping = [
        ("hello", "Hello / Lifecycle"),
        ("jsonSerialization", "JSON Serialization"),
        ("payload", "Payload Size"),
        ("staticFile", "Static File"),
        ("routes", "Route Count"),
    ]
    for prefix, label in mapping:
        if short.startswith(prefix):
            return label
    return short


def table_header(include_benchmark: bool) -> str:
    cells = []
    if include_benchmark:
        cells.append("<th>Benchmark</th>")
    cells.extend(
        [
            "<th>Version</th>",
            "<th>Winner</th>",
            "<th>Latest Score</th>",
            "<th>Score Error</th>",
            "<th>Unit</th>",
            "<th>Delta vs Prev %</th>",
            "<th>Delta vs Best %</th>",
            "<th>Best Band %</th>",
            "<th>Mean (last 8)</th>",
            "<th>Stdev (last 8)</th>",
            "<th>CV% (last 8)</th>",
            "<th>Samples</th>",
            "<th>Threads</th>",
            "<th>Forks</th>",
            "<th>Meas. Iter.</th>",
            "<th>Meas. Time</th>",
        ]
    )
    return "<tr>" + "".join(cells) + "</tr>"


def render_summary_row(row: dict, include_benchmark: bool) -> str:
    winner_badge = ""
    row_class = ""
    if row.get("strictBest"):
        winner_badge = "<span class='winner-badge' title='Highest latest score'>&#9733; Best</span>"
        row_class = "winner-row"
    elif row.get("coBest"):
        winner_badge = "<span class='cobest-badge' title='Within CV/error uncertainty band of the best score'>Near best</span>"
        row_class = "cobest-row"
    cells = []
    if include_benchmark:
        cells.append(f"<td>{escape(row['benchmark'])}</td>")
    cells.extend(
        [
            f"<td>{escape(row['version'])}</td>",
            f"<td>{winner_badge}</td>",
            f"<td>{safe_float(row['latestScore'])}</td>",
            f"<td>{safe_float(row['latestScoreError'])}</td>",
            f"<td>{escape(row['scoreUnit'])}</td>",
            f"<td>{safe_float(row['deltaVsPreviousPercent'], 2)}</td>",
            f"<td>{safe_float(row.get('deltaFromBestPercent'), 2)}</td>",
            f"<td>{safe_float(row.get('bestBandPercent'), 2)}</td>",
            f"<td>{safe_float(row['meanLast8'])}</td>",
            f"<td>{safe_float(row['stdevLast8'])}</td>",
            f"<td>{safe_float(row['cvLast8Percent'], 2)}</td>",
            f"<td>{row['samples']}</td>",
            f"<td>{row['threads'] if row['threads'] is not None else ''}</td>",
            f"<td>{row['forks'] if row['forks'] is not None else ''}</td>",
            f"<td>{row['measurementIterations'] if row['measurementIterations'] is not None else ''}</td>",
            f"<td>{escape(row['measurementTime'] or '')}</td>",
        ]
    )
    return "<tr class='" + row_class + "'>" + "".join(cells) + "</tr>"


def render_overview_table(rows: List[dict]) -> str:
    if not rows:
        return "<p>No benchmark rows yet.</p>"

    body_rows: List[str] = []
    previous_benchmark = None
    for row in rows:
        benchmark = row["benchmark"]
        if benchmark != previous_benchmark:
            body_rows.append(
                "<tr class='overview-divider'>"
                + f"<td colspan='17'>{escape(benchmark_group_label(benchmark))}: {escape(benchmark)}</td>"
                + "</tr>"
            )
        previous_benchmark = benchmark
        body_rows.append(render_summary_row(row, include_benchmark=True))

    body = "\n".join(body_rows)
    return (
        "<div class='summary-wrap overview-wrap'>"
        + "<table><thead>"
        + table_header(include_benchmark=True)
        + "</thead><tbody>"
        + body
        + "</tbody></table>"
        + "</div>"
    )


def benchmark_tab_id(benchmark: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", benchmark).strip("-").lower()
    return f"benchmark-tab-{slug}"


def render_benchmark_tabs(rows: List[dict]) -> str:
    if not rows:
        return "<p>No benchmark rows yet.</p>"

    grouped: Dict[str, List[dict]] = {}
    for row in rows:
        grouped.setdefault(row["benchmark"], []).append(row)

    benchmarks = sorted(grouped.keys())
    buttons = []
    panels = []

    for index, benchmark in enumerate(benchmarks):
        tab_id = benchmark_tab_id(benchmark)
        short = benchmark.split(".")[-1]
        title = f"{benchmark_group_label(benchmark)}: {short}"
        active_class = " active" if index == 0 else ""
        active_bool = "true" if index == 0 else "false"
        hidden_attr = "" if index == 0 else " hidden"

        buttons.append(
            f"<button type='button' class='tab-button{active_class}' data-tab-button='{escape(tab_id)}' aria-selected='{active_bool}'>{escape(title)}</button>"
        )

        table_rows = "\n".join(render_summary_row(row, include_benchmark=False) for row in grouped[benchmark])
        panels.append(
            "<section class='tab-panel"
            + active_class
            + f"' data-tab-panel='{escape(tab_id)}'{hidden_attr}>"
            + f"<h3>{escape(benchmark_group_label(benchmark))}: {escape(benchmark)}</h3>"
            + "<div class='summary-wrap'>"
            + "<table><thead>"
            + table_header(include_benchmark=False)
            + "</thead><tbody>"
            + table_rows
            + "</tbody></table>"
            + "</div>"
            + "<div class='panel-chart'>"
            + f"<section class='chart-card'><div class='chart-host' data-chart-key='{escape(benchmark)}'></div></section>"
            + "</div>"
            + "</section>"
        )

    return (
        "<section class='tab-shell'>"
        + "<div class='tab-buttons'>"
        + "".join(buttons)
        + "</div>"
        + "<div class='tab-panels'>"
        + "".join(panels)
        + "</div>"
        + "</section>"
    )


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
    chart_data_map: Dict[str, dict],
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

    benchmark_tabs_html = render_benchmark_tabs(rows)
    overview_table_html = render_overview_table(rows)
    sidebar = render_sidebar(run_timeline, active_run_id, latest_run_id, root_rel)
    chart_data_json = json.dumps(chart_data_map).replace("</", "<\\/")

    mode_title = "Latest cumulative report" if active_run_id is None else f"Snapshot for {active_run_id}"

    explain_html = """
      <section class='help-card'>
        <h2>How To Read This</h2>
        <p>Pretend each benchmark is a race. The fastest racer wins.</p>
        <p><strong>Higher score is better.</strong> Score is <code>ops/ms</code>: how many requests finished in one millisecond.</p>
        <p><strong>Benchmark Settings</strong> show what this specific run actually executed.</p>
        <p><strong>&#9733; Best</strong> marks the strict top score in that benchmark.</p>
        <p><strong>Near best</strong> means the score is within a CV/error uncertainty band of the top score.</p>
        <p><strong>Delta vs Prev %</strong> compares this run to the previous run for the same version and benchmark.</p>
        <p><strong>CV%</strong> is consistency across recent runs (not the same as Delta vs Prev): lower means more stable numbers over time.</p>
        <p><strong>Chart tips:</strong> hover a line point to see timestamp + exact score.</p>
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
      width: 100%;
      margin: 0;
      display: grid;
      grid-template-columns: 270px minmax(0, 1fr);
      gap: 1rem;
      padding: 1rem 1.2rem;
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
      width: 100%;
    }}
    .tab-shell {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      padding: .75rem;
      margin-bottom: .9rem;
    }}
    .tab-buttons {{
      display: flex;
      flex-wrap: wrap;
      gap: .5rem;
      margin-bottom: .65rem;
    }}
    .tab-button {{
      border: 1px solid #b8d2e8;
      background: #f4faff;
      color: #21405e;
      border-radius: 999px;
      padding: .3rem .72rem;
      font-size: .82rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .tab-button.active {{
      border-color: var(--accent);
      background: #deeff8;
      color: #173752;
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .tab-panel h3 {{ margin-bottom: .5rem; }}
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
    .cobest-row {{ background: #f9f5e8; }}
    .cobest-row td:first-child {{ border-left: 4px solid #a6782f; }}
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
    .cobest-badge {{
      display: inline-block;
      border: 1px solid #8a6a2d;
      border-radius: 999px;
      padding: .08rem .44rem;
      font-size: .72rem;
      font-weight: 700;
      background: #f2e9cf;
      color: #5f4a1f;
    }}
    .chart-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: .75rem;
    }}
    .chart-host {{
      width: 100%;
      height: 340px;
      min-height: 240px;
    }}
    .panel-chart {{ margin-top: .7rem; }}
    .section-note {{
      margin: .2rem 0 .65rem;
      color: var(--muted);
      font-size: .9rem;
    }}
    .overview-note {{
      margin: .2rem 0 .65rem;
      color: var(--muted);
      font-size: .88rem;
    }}
    .overview-wrap {{
      max-height: 58vh;
    }}
    .overview-divider td {{
      background: #f3f8fd;
      color: #294863;
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: .02em;
      border-top: 2px solid #c7d8e9;
      border-bottom: 1px solid #dbe8f3;
      padding-top: .3rem;
      padding-bottom: .3rem;
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

      <h2>Per-Benchmark Results</h2>
      <p class=\"section-note\">Each tab shows one benchmark with the latest per-version table and the trend chart directly below it.</p>
      {benchmark_tabs_html}

      <h2>All Benchmarks Overview</h2>
      <p class=\"overview-note\">This is the same latest table data as the tabs above, collected into one table for quick scanning.</p>
      {overview_table_html}

      <footer>Higher score is better in throughput mode. Use Delta vs Best % plus Best Band % to spot statistically close results that can be treated as tied.</footer>
    </main>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <script>
    (() => {{
      const chartData = {chart_data_json};
      const buttons = Array.from(document.querySelectorAll('[data-tab-button]'));
      const panels = Array.from(document.querySelectorAll('[data-tab-panel]'));
      if (!buttons.length || !panels.length) {{
        return;
      }}

      const chartInstances = new Map();

      const buildOption = (spec) => {{
        const labels = spec.runs.map((run) => run.label);
        const fullLabels = spec.runs.map((run) => run.fullLabel);
        return {{
          animation: false,
          color: spec.series.map((line) => line.color),
          grid: {{ left: 52, right: 20, top: 44, bottom: 44, containLabel: true }},
          legend: {{ top: 0, left: 0, type: 'scroll' }},
          tooltip: {{
            trigger: 'axis',
            confine: true,
            axisPointer: {{ type: 'cross' }},
            formatter: (params) => {{
              if (!params || !params.length) {{
                return '';
              }}
              const dataIndex = params[0].dataIndex;
              const lines = [fullLabels[dataIndex] || labels[dataIndex] || ''];
              params.forEach((item) => {{
                if (item.value === null || item.value === undefined || item.value === '-') {{
                  return;
                }}
                const numeric = Number(item.value);
                const valueText = Number.isFinite(numeric) ? numeric.toFixed(4) : String(item.value);
                lines.push(`${{item.seriesName}}: ${{valueText}} ${{spec.scoreUnit}}`);
              }});
              return lines.join('<br/>');
            }},
          }},
          xAxis: {{
            type: 'category',
            boundaryGap: false,
            data: labels,
            axisLabel: {{ hideOverlap: true }},
            axisTick: {{ alignWithLabel: true }},
          }},
          yAxis: {{
            type: 'value',
            name: spec.scoreUnit,
            splitLine: {{ lineStyle: {{ color: '#dce9f6' }} }},
          }},
          series: spec.series.map((line) => ({{
            name: line.name,
            type: 'line',
            data: line.data,
            showSymbol: true,
            symbolSize: 6,
            connectNulls: false,
            smooth: false,
            lineStyle: {{ width: 2 }},
            itemStyle: {{ color: line.color }},
            emphasis: {{ focus: 'series' }},
          }})),
        }};
      }};

      const renderPanelChart = (panel) => {{
        const host = panel.querySelector('.chart-host');
        if (!host) {{
          return;
        }}
        const key = host.dataset.chartKey;
        const spec = chartData[key];
        if (!spec) {{
          host.textContent = 'No trend data yet.';
          return;
        }}
        if (typeof echarts === 'undefined') {{
          host.textContent = 'Chart library failed to load.';
          return;
        }}

        let chart = chartInstances.get(key);
        if (!chart) {{
          chart = echarts.init(host);
          chartInstances.set(key, chart);
        }}

        chart.setOption(buildOption(spec), true);
        chart.resize();
      }};

      const setActive = (tabId) => {{
        buttons.forEach((button) => {{
          const active = button.dataset.tabButton === tabId;
          button.classList.toggle('active', active);
          button.setAttribute('aria-selected', active ? 'true' : 'false');
        }});

        panels.forEach((panel) => {{
          const active = panel.dataset.tabPanel === tabId;
          panel.classList.toggle('active', active);
          panel.hidden = !active;
        }});

        const activePanel = panels.find((panel) => panel.dataset.tabPanel === tabId);
        if (activePanel) {{
          renderPanelChart(activePanel);
        }}
      }};

      buttons.forEach((button) => {{
        button.addEventListener('click', () => setActive(button.dataset.tabButton));
      }});

      const initiallyActive = buttons.find((button) => button.classList.contains('active')) || buttons[0];
      if (initiallyActive) {{
        setActive(initiallyActive.dataset.tabButton);
      }}

      window.addEventListener('resize', () => {{
        chartInstances.forEach((chart) => chart.resize());
      }});
    }})();
  </script>
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
    chart_data_map = build_chart_data_map(records)
    output_path.write_text(
        build_html(
            repo=repo,
            rows=rows,
            chart_data_map=chart_data_map,
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
