#!/usr/bin/env python3
import argparse
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple

METADATA_URL = "https://repo1.maven.org/maven2/io/javalin/javalin/maven-metadata.xml"


def parse_version(version: str) -> Tuple[int, int, int, str]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[.-]?([A-Za-z].*))?", version)
    if not match:
        raise ValueError(f"Unsupported version format: {version}")
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), suffix or ""


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
    return major, minor, patch, stability_rank, suffix


def parse_minimum(value: str) -> Tuple[int, int, int]:
    if not is_stable(value):
        raise SystemExit("--minimum must be a stable semantic version, e.g. 5.0.0")
    return stable_tuple(value)


def fetch_versions() -> List[str]:
    with urllib.request.urlopen(METADATA_URL, timeout=30) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    return [element.text.strip() for element in root.findall("./versioning/versions/version") if element.text]


def select_versions(
    versions: List[str],
    minimum: Tuple[int, int, int],
    include_all_latest_majors: int,
    latest_minors_per_major: int,
    include_latest_per_major: bool,
    include_prerelease_latest_major: bool,
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

    if include_prerelease_latest_major:
        parsed_all = []
        for version in versions:
            try:
                parsed_all.append((version, parse_version(version)))
            except ValueError:
                continue
        if parsed_all:
            latest_major_seen = max(parsed[0] for _, parsed in parsed_all)
            prereleases = [
                version
                for version, (major, minor, patch, suffix) in parsed_all
                if major == latest_major_seen and suffix != ""
            ]
            selected.update(prereleases)

    return sorted(selected, key=version_sort_key)


def build_header(
    minimum_text: str,
    include_all_latest_majors: int,
    latest_minors_per_major: int,
    include_latest_per_major: bool,
    include_prerelease_latest_major: bool,
) -> List[str]:
    lines = [
        "# Javalin releases from Maven Central (auto-generated via scripts/update_versions_from_maven.py).",
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
    else:
        lines.append("# - prereleases (alpha/beta/rc) excluded")

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
    )

    header = build_header(
        minimum_text=args.minimum,
        include_all_latest_majors=args.include_all_latest_majors,
        latest_minors_per_major=args.latest_minors_per_major,
        include_latest_per_major=include_latest_per_major,
        include_prerelease_latest_major=args.include_prerelease_latest_major,
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
