#!/usr/bin/env python3
import argparse
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run_command(command):
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        text = completed.stdout.strip()
        if not text:
            text = completed.stderr.strip()
        return text
    except Exception as exc:  # pragma: no cover - best effort metadata
        return f"<unavailable: {exc}>"


def parse_lscpu(output):
    data = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def parse_meminfo(path):
    values = {}
    try:
        for line in Path(path).read_text().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    except FileNotFoundError:
        values["error"] = "not found"
    return values


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except FileNotFoundError:
        return "<not found>"


def build_runner_info():
    env_keys = [
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_NUMBER",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_SHA",
        "GITHUB_REF_NAME",
        "RUNNER_NAME",
        "RUNNER_OS",
        "RUNNER_ARCH",
        "RUNNER_ENVIRONMENT",
        "ImageOS",
        "ImageVersion",
    ]

    lscpu_output = run_command(["lscpu"])
    meminfo = parse_meminfo("/proc/meminfo")

    return {
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "environment": {k: os.environ.get(k, "") for k in env_keys},
        "system": {
            "platform": platform.platform(),
            "pythonVersion": platform.python_version(),
            "kernel": run_command(["uname", "-a"]),
            "uptime": run_command(["uptime"]),
            "loadAverage": run_command(["cat", "/proc/loadavg"]),
        },
        "cpu": {
            "nproc": run_command(["nproc"]),
            "details": parse_lscpu(lscpu_output),
            "rawLscpu": lscpu_output,
            "cgroupCpuMax": read_text("/sys/fs/cgroup/cpu.max"),
            "cgroupCpuset": read_text("/sys/fs/cgroup/cpuset.cpus.effective"),
        },
        "memory": {
            "freeMb": run_command(["free", "-m"]),
            "meminfo": meminfo,
        },
        "java": {
            "javaVersion": run_command(["java", "-version"]),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Collect GitHub runner metadata")
    parser.add_argument("output", help="Output JSON file path")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_runner_info(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
