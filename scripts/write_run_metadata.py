#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Write benchmark run metadata")
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-timestamp-utc", required=True)
    parser.add_argument("--versions-json", required=True)
    parser.add_argument("--iterations", required=True, type=int)
    parser.add_argument("--iteration-time-ms", required=True, type=int)
    parser.add_argument("--forks", required=True, type=int)
    parser.add_argument("--threads", required=True, type=int)
    parser.add_argument("--repository", default="")
    parser.add_argument("--workflow", default="")
    parser.add_argument("--run-number", default="")
    parser.add_argument("--run-attempt", default="")
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--git-ref", default="")
    args = parser.parse_args()

    payload = {
        "runId": args.run_id,
        "runTimestampUtc": args.run_timestamp_utc,
        "repository": args.repository,
        "workflow": args.workflow,
        "runNumber": args.run_number,
        "runAttempt": args.run_attempt,
        "gitSha": args.git_sha,
        "gitRef": args.git_ref,
        "benchmarkSettings": {
            "versions": json.loads(args.versions_json),
            "iterations": args.iterations,
            "iterationTimeMs": args.iteration_time_ms,
            "forks": args.forks,
            "threads": args.threads,
            "resultFormat": "json",
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
