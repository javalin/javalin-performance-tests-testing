#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def fmt(value, digits=3):
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def main():
    parser = argparse.ArgumentParser(description="Print markdown summary from summary.json")
    parser.add_argument("summary_json", help="Path to summary.json")
    parser.add_argument("--limit", type=int, default=40, help="Max rows")
    args = parser.parse_args()

    payload = json.loads(Path(args.summary_json).read_text())
    rows = payload.get("rows", [])[: args.limit]

    print(f"## Benchmark summary ({payload.get('latestRunId', '')})")
    print("")
    print("| Version | Benchmark | Latest | Delta vs prev % | Mean(last8) | CV%(last8) | Samples |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for row in rows:
        print(
            "| "
            + f"{row.get('version', '')} | "
            + f"{row.get('benchmark', '')} | "
            + f"{fmt(row.get('latestScore'))} {row.get('scoreUnit', '')} | "
            + f"{fmt(row.get('deltaVsPreviousPercent'), 2)} | "
            + f"{fmt(row.get('meanLast8'))} | "
            + f"{fmt(row.get('cvLast8Percent'), 2)} | "
            + f"{row.get('samples', '')} |"
        )


if __name__ == "__main__":
    main()
