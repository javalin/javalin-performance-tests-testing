#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
  cat <<'EOF'
Usage: ./run-local-benchmarks.sh [options]

Runs Javalin benchmarks locally, stores a new historical run, and regenerates the static site.

Modes:
  --mode weekly   Production-like weekly run (default): auto-resolve versions from Maven policy
  --mode quick    Fast local smoke run: versions from config/pr-versions.txt

Options:
  --mode <quick|weekly>                  Run preset (default: weekly)
  --versions "<v1,v2 ...>"               Explicit versions (overrides mode defaults)
  --include-prerelease-latest-major      Include all alpha/beta/rc from latest major for auto version resolution
  --iterations <n>                       JMH warmup + measurement iterations
  --iteration-time-ms <n>                JMH warmup + measurement time in ms
  --forks <n>                            JMH forks
  --threads <n>                          JMH worker threads
  --history-root <dir>                   Directory containing run folders (default: local-history/runs)
  --site-dir <dir>                       Generated site output dir (default: local-history/site)
  --gradle-user-home <dir>               Gradle user home dir (default: .gradle-local-user-home)
  --gradle-project-cache-dir <dir>       Gradle project cache dir (default: .gradle-local-project-cache)
  --repository <name>                    Report repository label (default: derived from git remote)
  --run-id <id>                          Override run id (default: local-UTC-timestamp)
  --serve                                Start local HTTP server after generation
  --port <n>                             HTTP server port for --serve (default: 8000)
  --help                                 Show this help

Examples:
  ./run-local-benchmarks.sh
  ./run-local-benchmarks.sh --mode weekly --include-prerelease-latest-major
  ./run-local-benchmarks.sh --versions "5.6.5 6.7.0" --iterations 2 --iteration-time-ms 300
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

ensure_no_running_jmh() {
  local running
  running="$(pgrep -fa 'org.openjdk.jmh.runner.ForkedMain' || true)"
  if [[ -n "$running" ]]; then
    echo "Another JMH benchmark process is already running:" >&2
    echo "$running" >&2
    echo "Stop it before starting a new run:" >&2
    echo "  pkill -f org.openjdk.jmh.runner.ForkedMain" >&2
    echo "Then retry this script." >&2
    exit 1
  fi
}

select_compatible_jdk() {
  local selected=""
  local java_bin=""
  local java_major=""

  detect_java_major() {
    local java_exe="$1"
    local version_line=""
    if [[ ! -x "$java_exe" ]]; then
      echo ""
      return
    fi
    version_line="$("$java_exe" -version 2>&1 | head -n 1)"
    if [[ "$version_line" =~ \"1\.([0-9]+)\. ]]; then
      echo "${BASH_REMATCH[1]}"
      return
    fi
    if [[ "$version_line" =~ \"([0-9]+) ]]; then
      echo "${BASH_REMATCH[1]}"
      return
    fi
    echo ""
  }

  # Respect explicitly provided JAVA_HOME first.
  if [[ -n "${JAVA_HOME:-}" ]]; then
    java_bin="${JAVA_HOME}/bin/java"
    java_major="$(detect_java_major "$java_bin")"
    if [[ "$java_major" == "17" || "$java_major" == "21" ]]; then
      selected="$JAVA_HOME"
    fi
  fi

  # Next prefer currently active `java` on PATH (e.g. `mise exec java@17 -- ...`).
  if [[ -z "$selected" ]] && command -v java >/dev/null 2>&1; then
    java_bin="$(command -v java)"
    java_major="$(detect_java_major "$java_bin")"
    if [[ "$java_major" == "17" || "$java_major" == "21" ]]; then
      selected="${java_bin%/bin/java}"
    fi
  fi

  # Prefer Java selected by mise (if installed and configured).
  if [[ -z "$selected" ]] && command -v mise >/dev/null 2>&1; then
    local mise_java
    mise_java="$(mise which java 2>/dev/null || true)"
    if [[ -n "$mise_java" && -x "$mise_java" ]]; then
      java_major="$(detect_java_major "$mise_java")"
      if [[ "$java_major" == "17" || "$java_major" == "21" ]]; then
        selected="${mise_java%/bin/java}"
      fi
    fi
  fi

  local candidates=(
    "/usr/lib/jvm/java-21-openjdk-amd64"
    "/usr/lib/jvm/java-17-openjdk-amd64"
    "/usr/lib/jvm/java-21-openjdk"
    "/usr/lib/jvm/java-17-openjdk"
  )
  if [[ -z "$selected" ]]; then
    for candidate in "${candidates[@]}"; do
      if [[ -x "${candidate}/bin/java" ]]; then
        selected="${candidate}"
        break
      fi
    done
  fi

  if [[ -z "$selected" ]]; then
    echo "Could not find a compatible local JDK (21 or 17)." >&2
    echo "Install Java with mise ('mise install') or install JDK 21/17 system-wide, then rerun." >&2
    exit 1
  fi

  export JAVA_HOME="$selected"
  export PATH="${JAVA_HOME}/bin:${PATH}"
}

derive_repository() {
  local remote
  remote="$(git config --get remote.origin.url 2>/dev/null || true)"
  if [[ -z "$remote" ]]; then
    basename "$SCRIPT_DIR"
    return
  fi
  # Supports ssh and https remotes
  remote="${remote##*:}"
  remote="${remote#https://github.com/}"
  remote="${remote%.git}"
  echo "$remote"
}

to_abs_path() {
  local input="$1"
  if [[ "$input" = /* ]]; then
    echo "$input"
  else
    echo "${SCRIPT_DIR}/${input}"
  fi
}

MODE="weekly"
VERSIONS_RAW=""
INCLUDE_PRERELEASE_LATEST_MAJOR=false
ITERATIONS=""
ITERATION_TIME_MS=""
FORKS=""
THREADS=""
HISTORY_ROOT="${SCRIPT_DIR}/local-history/runs"
SITE_DIR="${SCRIPT_DIR}/local-history/site"
GRADLE_USER_HOME_DIR="${SCRIPT_DIR}/.gradle-local-user-home"
GRADLE_PROJECT_CACHE_DIR="${SCRIPT_DIR}/.gradle-local-project-cache"
REPOSITORY="$(derive_repository)"
RUN_ID="local-$(date -u +%Y%m%dT%H%M%SZ)"
RUN_TIMESTAMP_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SERVE=false
PORT=8000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --versions)
      VERSIONS_RAW="${2:-}"
      shift 2
      ;;
    --include-prerelease-latest-major)
      INCLUDE_PRERELEASE_LATEST_MAJOR=true
      shift
      ;;
    --iterations)
      ITERATIONS="${2:-}"
      shift 2
      ;;
    --iteration-time-ms)
      ITERATION_TIME_MS="${2:-}"
      shift 2
      ;;
    --forks)
      FORKS="${2:-}"
      shift 2
      ;;
    --threads)
      THREADS="${2:-}"
      shift 2
      ;;
    --history-root)
      HISTORY_ROOT="${2:-}"
      shift 2
      ;;
    --site-dir)
      SITE_DIR="${2:-}"
      shift 2
      ;;
    --gradle-user-home)
      GRADLE_USER_HOME_DIR="${2:-}"
      shift 2
      ;;
    --gradle-project-cache-dir)
      GRADLE_PROJECT_CACHE_DIR="${2:-}"
      shift 2
      ;;
    --repository)
      REPOSITORY="${2:-}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --serve)
      SERVE=true
      shift
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$MODE" != "quick" && "$MODE" != "weekly" ]]; then
  echo "--mode must be 'quick' or 'weekly'" >&2
  exit 1
fi

if [[ -z "$ITERATIONS" ]]; then
  if [[ "$MODE" == "weekly" ]]; then
    ITERATIONS="10"
  else
    ITERATIONS="2"
  fi
fi
if [[ -z "$ITERATION_TIME_MS" ]]; then
  if [[ "$MODE" == "weekly" ]]; then
    ITERATION_TIME_MS="1000"
  else
    ITERATION_TIME_MS="300"
  fi
fi
if [[ -z "$FORKS" ]]; then
  if [[ "$MODE" == "weekly" ]]; then
    FORKS="2"
  else
    FORKS="1"
  fi
fi
if [[ -z "$THREADS" ]]; then
  THREADS="4"
fi

HISTORY_ROOT="$(to_abs_path "$HISTORY_ROOT")"
SITE_DIR="$(to_abs_path "$SITE_DIR")"
GRADLE_USER_HOME_DIR="$(to_abs_path "$GRADLE_USER_HOME_DIR")"
GRADLE_PROJECT_CACHE_DIR="$(to_abs_path "$GRADLE_PROJECT_CACHE_DIR")"

require_cmd python3
require_cmd git

if [[ ! -x "./gradlew" ]]; then
  echo "Missing executable ./gradlew at repository root" >&2
  exit 1
fi

ensure_no_running_jmh
select_compatible_jdk

mkdir -p "$GRADLE_USER_HOME_DIR" "$GRADLE_PROJECT_CACHE_DIR"
export GRADLE_USER_HOME="$GRADLE_USER_HOME_DIR"

VERSIONS_JSON=""
if [[ -n "$VERSIONS_RAW" ]]; then
  VERSIONS_JSON="$(python3 scripts/resolve_versions.py --raw "$VERSIONS_RAW" --config config/versions.txt)"
else
  if [[ "$MODE" == "weekly" ]]; then
    auto_args=(
      --output /tmp/local-auto-versions.txt
      --minimum 1.0.0
      --include-all-latest-majors 2
      --latest-minors-per-major 3
      --no-include-latest-per-major
      --latest-prerelease-count 2
      --include-latest-snapshot
      --json
    )
    if [[ "$INCLUDE_PRERELEASE_LATEST_MAJOR" == "true" ]]; then
      auto_args+=(--include-prerelease-latest-major)
    fi

    set +e
    VERSIONS_JSON="$(python3 scripts/update_versions_from_maven.py "${auto_args[@]}")"
    auto_status=$?
    set -e

    if [[ $auto_status -ne 0 || -z "$VERSIONS_JSON" || "$VERSIONS_JSON" == "[]" ]]; then
      echo "Auto version resolution failed; falling back to config/versions.txt"
      VERSIONS_JSON="$(python3 scripts/resolve_versions.py --config config/versions.txt)"
    fi
  else
    VERSIONS_JSON="$(python3 scripts/resolve_versions.py --config config/pr-versions.txt)"
  fi
fi

mkdir -p "$HISTORY_ROOT"
RUN_DIR="${HISTORY_ROOT}/${RUN_ID}"
if [[ -e "$RUN_DIR" ]]; then
  echo "Run directory already exists: $RUN_DIR" >&2
  echo "Use --run-id with a unique value." >&2
  exit 1
fi
mkdir -p "$RUN_DIR/results"

TMP_VERSIONS_FILE="$(mktemp)"
trap 'rm -f "$TMP_VERSIONS_FILE"' EXIT
python3 scripts/json_to_lines.py "$VERSIONS_JSON" > "$TMP_VERSIONS_FILE"
VERSION_COUNT="$(wc -l < "$TMP_VERSIONS_FILE" | tr -d ' ')"

echo "== Local Benchmark Run =="
echo "mode=${MODE}"
echo "runId=${RUN_ID}"
echo "runTimestampUtc=${RUN_TIMESTAMP_UTC}"
echo "repository=${REPOSITORY}"
echo "versions=${VERSION_COUNT}"
echo "iterations=${ITERATIONS} iterationTimeMs=${ITERATION_TIME_MS} forks=${FORKS} threads=${THREADS}"
echo "historyRoot=${HISTORY_ROOT}"
echo "siteDir=${SITE_DIR}"
echo "gradleUserHome=${GRADLE_USER_HOME_DIR}"
echo "gradleProjectCacheDir=${GRADLE_PROJECT_CACHE_DIR}"
echo "javaHome=${JAVA_HOME}"
echo "javaVersion=$("${JAVA_HOME}/bin/java" -version 2>&1 | head -n 1)"
echo

python3 scripts/collect_runner_info.py "${RUN_DIR}/runner-info.json"
python3 scripts/write_run_metadata.py \
  --output "${RUN_DIR}/run-metadata.json" \
  --run-id "${RUN_ID}" \
  --run-timestamp-utc "${RUN_TIMESTAMP_UTC}" \
  --versions-json "${VERSIONS_JSON}" \
  --iterations "${ITERATIONS}" \
  --iteration-time-ms "${ITERATION_TIME_MS}" \
  --forks "${FORKS}" \
  --threads "${THREADS}" \
  --repository "${REPOSITORY}" \
  --workflow "local-script" \
  --run-number "0" \
  --run-attempt "1" \
  --git-sha "$(git rev-parse --short HEAD 2>/dev/null || echo local)" \
  --git-ref "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo local)"

while IFS= read -r version; do
  [[ -z "$version" ]] && continue
  echo ">> Running benchmark for Javalin ${version}"
  ./gradlew --no-daemon --project-cache-dir "$GRADLE_PROJECT_CACHE_DIR" clean benchmark \
    -PjavalinVersion="${version}" \
    -Piterations="${ITERATIONS}" \
    -PiterationTime="${ITERATION_TIME_MS}" \
    -Pthreads="${THREADS}" \
    -Pforks="${FORKS}" \
    -PresultFormat="json"
  cp "results/${version}.json" "${RUN_DIR}/results/${version}.json"
done < "$TMP_VERSIONS_FILE"

mkdir -p "$SITE_DIR"
python3 scripts/generate_pages.py \
  --history-root "$HISTORY_ROOT" \
  --output-dir "$SITE_DIR" \
  --repository "$REPOSITORY"

python3 - <<PY
import json
from pathlib import Path
summary = json.loads(Path("${SITE_DIR}/summary.json").read_text())
rows = summary.get("rows", [])
print("")
print("== Report Updated ==")
print("latestRunId:", summary.get("latestRunId"))
print("benchmarks:", len(rows))
sample_counts = sorted({row.get("samples") for row in rows})
print("distinctSamples:", sample_counts)
print("index:", "${SITE_DIR}/index.html")
PY

if [[ "$SERVE" == "true" ]]; then
  echo ""
  echo "Serving site at http://localhost:${PORT}"
  python3 -m http.server "$PORT" --directory "$SITE_DIR"
else
  echo ""
  echo "Done. Open file://${SITE_DIR}/index.html"
  echo "Or run: python3 -m http.server ${PORT} --directory ${SITE_DIR}"
fi
