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
    parser.add_argument("--source-repository", default="")
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--source-ref", default="")
    parser.add_argument("--source-pr-number", default="")
    parser.add_argument("--source-tarball-url", default="")
    parser.add_argument("--trigger-repository", default="")
    parser.add_argument("--trigger-pr-number", default="")
    parser.add_argument("--trigger-pr-url", default="")
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

    source_snapshot = {
        "repository": args.source_repository,
        "sha": args.source_sha,
        "ref": args.source_ref,
        "pullRequestNumber": args.source_pr_number,
        "tarballUrl": args.source_tarball_url,
    }
    if any(value for value in source_snapshot.values()):
        payload["sourceSnapshot"] = source_snapshot

    trigger_context = {
        "repository": args.trigger_repository,
        "pullRequestNumber": args.trigger_pr_number,
        "pullRequestUrl": args.trigger_pr_url,
    }
    if any(value for value in trigger_context.values()):
        payload["triggerContext"] = trigger_context

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
