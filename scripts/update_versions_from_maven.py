#!/usr/bin/env python3
import argparse
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple

METADATA_URL = "https://repo1.maven.org/maven2/io/javalin/javalin/maven-metadata.xml"
SNAPSHOT_METADATA_URL = "https://maven.reposilite.com/snapshots/io/javalin/javalin/maven-metadata.xml"


def fetch_xml(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "javalin-performance-tests-version-resolver/1.0",
            "Accept": "application/xml,text/xml,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def parse_version(version: str) -> Tuple[int, int, int, str]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[.-]?([A-Za-z].*))?", version)
    if not match:
        raise ValueError(f"Unsupported version format: {version}")
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), suffix or ""


def parse_prerelease_suffix(suffix: str) -> Tuple[int, int, str]:
    normalized = suffix.lower()
    snapshot = "-snapshot" in normalized or normalized.endswith("snapshot")
    cleaned = normalized.replace("-snapshot", "").replace(".snapshot", "").strip("-.")

    if "alpha" in cleaned:
        stage = 0
    elif "beta" in cleaned:
        stage = 1
    elif "rc" in cleaned:
        stage = 2
    else:
        stage = 3

    number_match = re.search(r"(\d+)", cleaned)
    stage_number = int(number_match.group(1)) if number_match else -1

    # Snapshot variants should sort after their base prerelease stage when comparing.
    snapshot_rank = 1 if snapshot else 0
    return stage, stage_number * 2 + snapshot_rank, cleaned


def is_stable(version: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", version))


def stable_tuple(version: str) -> Tuple[int, int, int]:
    major, minor, patch, suffix = parse_version(version)
    if suffix:
        raise ValueError(f"Not a stable version: {version}")
    return major, minor, patch


def version_sort_key(version: str):
    major, minor, patch, suffix = parse_version(version)
    stability_rank = 1 if suffix == "" else 0
    suffix_stage, suffix_stage_number, suffix_text = parse_prerelease_suffix(suffix) if suffix else (99, 99, "")
    return major, minor, patch, stability_rank, suffix_stage, suffix_stage_number, suffix_text


def parse_minimum(value: str) -> Tuple[int, int, int]:
    if not is_stable(value):
        raise SystemExit("--minimum must be a stable semantic version, e.g. 5.0.0")
    return stable_tuple(value)


def fetch_versions() -> List[str]:
    xml_data = fetch_xml(METADATA_URL)
    root = ET.fromstring(xml_data)
    return [element.text.strip() for element in root.findall("./versioning/versions/version") if element.text]


def fetch_snapshot_versions() -> List[str]:
    xml_data = fetch_xml(SNAPSHOT_METADATA_URL)
    root = ET.fromstring(xml_data)
    return [element.text.strip() for element in root.findall("./versioning/versions/version") if element.text]


def select_versions(
    versions: List[str],
    minimum: Tuple[int, int, int],
    include_all_latest_majors: int,
    latest_minors_per_major: int,
    include_latest_per_major: bool,
    include_prerelease_latest_major: bool,
    latest_prerelease_count: int,
    include_latest_snapshot: bool,
) -> List[str]:
    stable_versions = [
        version
        for version in versions
        if is_stable(version) and stable_tuple(version) >= minimum
    ]

    if not stable_versions:
        raise SystemExit("No stable versions matched filters")

    stable_by_major = {}
    for version in stable_versions:
        major = stable_tuple(version)[0]
        stable_by_major.setdefault(major, []).append(version)

    for major in stable_by_major:
        stable_by_major[major].sort(key=stable_tuple)

    sorted_stable_majors = sorted(stable_by_major.keys())
    latest_major_slice = sorted_stable_majors[-max(include_all_latest_majors, 0):] if include_all_latest_majors > 0 else []

    selected = set()

    for major in latest_major_slice:
        major_versions = stable_by_major[major]
        if latest_minors_per_major > 0:
            latest_patch_by_minor = {}
            for version in major_versions:
                _, minor, patch = stable_tuple(version)
                current = latest_patch_by_minor.get(minor)
                if current is None:
                    latest_patch_by_minor[minor] = version
                else:
                    _, _, current_patch = stable_tuple(current)
                    if patch > current_patch:
                        latest_patch_by_minor[minor] = version
            for minor in sorted(latest_patch_by_minor.keys())[-latest_minors_per_major:]:
                selected.add(latest_patch_by_minor[minor])
        else:
            selected.update(major_versions)

    if include_latest_per_major:
        for major in sorted_stable_majors:
            selected.add(stable_by_major[major][-1])

    parsed_all = []
    for version in versions:
        try:
            parsed_all.append((version, parse_version(version)))
        except ValueError:
            continue

    latest_major_seen = None
    if parsed_all:
        latest_major_seen = max(parsed[0] for _, parsed in parsed_all)

    if latest_major_seen is not None and (include_prerelease_latest_major or latest_prerelease_count > 0):
        prereleases = [
            version
            for version, (major, _, _, suffix) in parsed_all
            if major == latest_major_seen and suffix != "" and "snapshot" not in suffix.lower()
        ]
        prereleases = sorted(prereleases, key=version_sort_key)

        if include_prerelease_latest_major:
            selected.update(prereleases)
        elif latest_prerelease_count > 0:
            rc_candidates = [version for version in prereleases if "rc" in parse_version(version)[3].lower()]
            chosen: List[str] = []
            if rc_candidates:
                chosen.extend(sorted(rc_candidates, key=version_sort_key)[-latest_prerelease_count:])
            if len(chosen) < latest_prerelease_count:
                for candidate in reversed(prereleases):
                    if candidate in chosen:
                        continue
                    chosen.append(candidate)
                    if len(chosen) >= latest_prerelease_count:
                        break
            selected.update(chosen)

    if include_latest_snapshot:
        try:
            snapshot_versions = fetch_snapshot_versions()
        except Exception:
            snapshot_versions = []
        parsed_snapshots = []
        for version in snapshot_versions:
            if "snapshot" not in version.lower():
                continue
            try:
                parse_version(version)
                parsed_snapshots.append(version)
            except ValueError:
                continue
        if parsed_snapshots:
            selected.add(sorted(parsed_snapshots, key=version_sort_key)[-1])

    return sorted(selected, key=version_sort_key)


def build_header(
    minimum_text: str,
    include_all_latest_majors: int,
    latest_minors_per_major: int,
    include_latest_per_major: bool,
    include_prerelease_latest_major: bool,
    latest_prerelease_count: int,
    include_latest_snapshot: bool,
) -> List[str]:
    lines = [
        "# Javalin versions from Maven Central (plus optional snapshot metadata, auto-generated via scripts/update_versions_from_maven.py).",
        "# Policy:",
        f"# - include stable releases from the latest {include_all_latest_majors} stable major versions",
    ]
    if latest_minors_per_major > 0:
        lines.append(
            f"# - for each selected major: keep latest patch for the latest {latest_minors_per_major} minors"
        )
    else:
        lines.append("# - for each selected major: keep all stable releases")

    if include_latest_per_major:
        lines.append("# - include the latest stable release from every major version")
    else:
        lines.append("# - do not include latest-per-major extras")

    if include_prerelease_latest_major:
        lines.append("# - include prereleases (alpha/beta/rc) from the numerically latest major")
    elif latest_prerelease_count > 0:
        lines.append(
            f"# - include latest {latest_prerelease_count} prereleases from latest major (RC-preferred)"
        )
    else:
        lines.append("# - prereleases (alpha/beta/rc) excluded")

    if include_latest_snapshot:
        lines.append("# - include latest snapshot from https://maven.reposilite.com/snapshots")
    else:
        lines.append("# - snapshots excluded")

    lines.append(f"# - minimum stable cutoff: >= {minimum_text}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="Select Javalin benchmark versions from Maven metadata")
    parser.add_argument("--output", default="config/versions.txt", help="Output file path")
    parser.add_argument("--minimum", default="1.0.0", help="Minimum stable version (inclusive)")
    parser.add_argument(
        "--include-all-latest-majors",
        type=int,
        default=2,
        help="Include all stable versions for this many latest stable major lines",
    )
    parser.add_argument(
        "--latest-minors-per-major",
        type=int,
        default=0,
        help="If >0, keep only the latest patch from the latest N minors for each selected major",
    )
    parser.add_argument(
        "--no-include-latest-per-major",
        action="store_true",
        help="Disable adding latest stable version from each major",
    )
    parser.add_argument(
        "--include-prerelease-latest-major",
        action="store_true",
        help="Include prerelease versions (alpha/beta/rc) from the numerically latest major",
    )
    parser.add_argument(
        "--latest-prerelease-count",
        type=int,
        default=0,
        help="If >0, include latest N prereleases from numerically latest major (RC-preferred)",
    )
    parser.add_argument(
        "--include-latest-snapshot",
        action="store_true",
        help="Include latest snapshot from snapshot repository metadata",
    )
    parser.add_argument("--json", action="store_true", help="Print selected versions as JSON")
    args = parser.parse_args()

    minimum = parse_minimum(args.minimum)
    include_latest_per_major = not args.no_include_latest_per_major

    versions = fetch_versions()
    selected = select_versions(
        versions=versions,
        minimum=minimum,
        include_all_latest_majors=args.include_all_latest_majors,
        latest_minors_per_major=args.latest_minors_per_major,
        include_latest_per_major=include_latest_per_major,
        include_prerelease_latest_major=args.include_prerelease_latest_major,
        latest_prerelease_count=args.latest_prerelease_count,
        include_latest_snapshot=args.include_latest_snapshot,
    )

    header = build_header(
        minimum_text=args.minimum,
        include_all_latest_majors=args.include_all_latest_majors,
        latest_minors_per_major=args.latest_minors_per_major,
        include_latest_per_major=include_latest_per_major,
        include_prerelease_latest_major=args.include_prerelease_latest_major,
        latest_prerelease_count=args.latest_prerelease_count,
        include_latest_snapshot=args.include_latest_snapshot,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join([*header, *selected, ""]))

    if args.json:
        print(json.dumps(selected))
    else:
        print(f"Wrote {len(selected)} versions to {output_path}")


if __name__ == "__main__":
    main()
