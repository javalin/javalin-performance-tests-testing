#!/usr/bin/env bash
set -euo pipefail

WORKFLOW_FILE=".github/workflows/benchmark-pages.yml"

usage() {
  cat <<'EOF'
Usage: ./run-github-weekly-benchmark.sh [options]

Dispatches the weekly benchmark workflow on GitHub Actions using workflow_dispatch.

Options:
  --repo <owner/repo>                    GitHub repository (default: derived from git remote)
  --ref <branch-or-tag>                  Git ref to run on (default: current branch)
  --versions "<v1,v2 ...>"               Optional explicit versions override
  --include-prerelease-latest-major      Include alpha/beta/rc from latest major
  --iterations <n>                       JMH iterations (default: 10)
  --iteration-time-ms <n>                Iteration time in ms (default: 1000)
  --forks <n>                            JMH forks (default: 2)
  --threads <n>                          JMH threads (default: 4)
  --watch                                Watch run progress after dispatch
  --help                                 Show this help

Examples:
  ./run-github-weekly-benchmark.sh
  ./run-github-weekly-benchmark.sh --watch
  ./run-github-weekly-benchmark.sh --versions "5.6.5,6.7.0" --iterations 6 --forks 1
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

derive_repo() {
  local remote
  remote="$(git config --get remote.origin.url 2>/dev/null || true)"
  if [[ -z "$remote" ]]; then
    echo "Could not derive repository from git remote. Use --repo owner/name." >&2
    exit 1
  fi
  remote="${remote##*:}"
  remote="${remote#https://github.com/}"
  remote="${remote%.git}"
  echo "$remote"
}

derive_ref() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main"
}

REPO=""
REF=""
VERSIONS=""
INCLUDE_PRERELEASE_LATEST_MAJOR=false
ITERATIONS="10"
ITERATION_TIME_MS="1000"
FORKS="2"
THREADS="4"
WATCH=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --ref)
      REF="${2:-}"
      shift 2
      ;;
    --versions)
      VERSIONS="${2:-}"
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
    --watch)
      WATCH=true
      shift
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

require_cmd gh
require_cmd git

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run: gh auth login" >&2
  exit 1
fi

if [[ -z "$REPO" ]]; then
  REPO="$(derive_repo)"
fi
if [[ -z "$REF" ]]; then
  REF="$(derive_ref)"
fi

dispatch_args=(
  workflow run "$WORKFLOW_FILE"
  --repo "$REPO"
  --ref "$REF"
  -f "iterations=$ITERATIONS"
  -f "iterationTimeMs=$ITERATION_TIME_MS"
  -f "forks=$FORKS"
  -f "threads=$THREADS"
)
if [[ -n "$VERSIONS" ]]; then
  dispatch_args+=(-f "versions=$VERSIONS")
fi
if [[ "$INCLUDE_PRERELEASE_LATEST_MAJOR" == "true" ]]; then
  dispatch_args+=(-f "includePrereleaseLatestMajor=true")
fi

echo "Dispatching $WORKFLOW_FILE on $REPO@$REF"
gh "${dispatch_args[@]}"

sleep 2
run_url="$(gh run list \
  --repo "$REPO" \
  --workflow "$WORKFLOW_FILE" \
  --branch "$REF" \
  --limit 1 \
  --json url \
  --jq '.[0].url // ""' 2>/dev/null || true)"
run_id="$(gh run list \
  --repo "$REPO" \
  --workflow "$WORKFLOW_FILE" \
  --branch "$REF" \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId // ""' 2>/dev/null || true)"

if [[ -n "$run_url" ]]; then
  echo "Run URL: $run_url"
else
  echo "Workflow dispatched. Open runs: https://github.com/$REPO/actions/workflows/benchmark-pages.yml"
fi

if [[ "$WATCH" == "true" && -n "$run_id" ]]; then
  gh run watch "$run_id" --repo "$REPO"
fi
