# javalin-performance-tests-testing
JMH performance benchmarks for Javalin versions, with automated GitHub Actions + GitHub Pages reporting.

## Local benchmark run
By default, benchmark results are written under `results/`.

One-command local run (recommended):
```sh
mise install
./run-local-benchmarks.sh
```
This uses production benchmark settings by default and appends a new run to local history before regenerating the site.
The scripts use project-local Gradle caches to reduce lock conflicts with other Gradle builds running on your machine.

Windows PowerShell:
```powershell
mise install
Set-ExecutionPolicy -Scope Process Bypass
./run-local-benchmarks.ps1
```
Alias wrapper names are also available: `run-local-benchmark.sh` and `run-local-benchmark.ps1`.

Run with defaults:
```sh
./gradlew clean benchmark -PjavalinVersion=4.6.4
```

Run with explicit tuning and JSON output:
```sh
./gradlew clean benchmark \
  -PjavalinVersion=4.6.4 \
  -Piterations=10 \
  -PiterationTime=2000 \
  -Pthreads=32 \
  -Pforks=2 \
  -PresultFormat=json
```

### Properties
- `javalinVersion`: dependency version to benchmark.
- `iterations`: warmup and measurement iterations.
- `iterationTime`: warmup and measurement time in milliseconds.
- `threads`: JMH worker threads.
- `forks`: JMH forks.
- `resultFormat`: JMH machine-readable format (`csv`, `json`, `scsv`, `latex`, `text`).
- `benchmark.http.connectTimeoutMs`: HTTP client connect timeout for benchmark traffic (default `15000`).
- `benchmark.http.readTimeoutMs`: HTTP client read timeout for benchmark traffic (default `120000`).
- `benchmark.http.writeTimeoutMs`: HTTP client write timeout for benchmark traffic (default `120000`).

If there is no version-specific wrapper in `src/main/external/<version>/`, the build falls back to `src/main/external/default/`.
Use `clean benchmark` when switching versions to avoid stale compiled classes between runs.

## Benchmark scenarios
Current suite includes:
- `hello`: hello/lifecycle/exception baseline flow.
- `payloadEmpty`: empty text payload.
- `payload100kb`, `payload1mb`: plain text payload sizes.
- `jsonSerializationSmall`, `jsonSerialization100kb`, `jsonSerialization1mb`: JSON serialization sizes.
- `staticFile100kb`, `staticFile1mb`: static-like raw byte responses.
- `routes10`, `routes100`, `routes1000`: route table size scenarios.
  Note: these route groups currently live in one benchmark app instance, so use them for relative trend tracking.

## Compare two CSV results locally
```sh
./gradlew compare -Pbaseline=1.0.0 -PjavalinVersion=3.0.0
```

## Plot results over time locally
Keep local history by storing each run under `runs/<run-id>/`:
```sh
RUN_ID="local-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "local-history/runs/$RUN_ID/results"
python3 scripts/collect_runner_info.py "local-history/runs/$RUN_ID/runner-info.json"
python3 scripts/write_run_metadata.py \
  --output "local-history/runs/$RUN_ID/run-metadata.json" \
  --run-id "$RUN_ID" \
  --run-timestamp-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --versions-json '["4.6.4","5.6.3"]' \
  --iterations 3 \
  --iteration-time-ms 500 \
  --forks 2 \
  --threads 4
```

Run benchmark versions and copy results:
```sh
./gradlew --no-daemon clean benchmark -PjavalinVersion=4.6.4 -Piterations=3 -PiterationTime=500 -Pforks=2 -Pthreads=4 -PresultFormat=json
cp results/4.6.4.json "local-history/runs/$RUN_ID/results/4.6.4.json"

./gradlew --no-daemon clean benchmark -PjavalinVersion=5.6.3 -Piterations=3 -PiterationTime=500 -Pforks=2 -Pthreads=4 -PresultFormat=json
cp results/5.6.3.json "local-history/runs/$RUN_ID/results/5.6.3.json"
```

Generate report with trend charts:
```sh
python3 scripts/generate_pages.py --history-root local-history/runs --output-dir local-history/site --repository javalin/javalin-performance-tests-testing
python3 -m http.server 8000 --directory local-history/site
```
Then open `http://localhost:8000`.

This now generates:
- `index.html`: latest cumulative report.
- `runs/<run-id>.html`: weekly snapshot pages (history up to that run).
- `summary.json`: machine-readable summary for automation.

## Reading the numbers
- JMH mode is throughput (`ops/ms`), so higher score is better.
- Compare versions on the same benchmark row (`payload1mb` for `4.6.4` vs `5.6.3`).
- `Delta vs Prev %` compares the latest run against the previous run of the same version+benchmark.
- `Winner` marks the highest-scoring version in each benchmark row.
- `Mean/Stdev/CV` show historical stability:
  - lower `CV%` means more stable measurements,
  - high `CV%` means noisy benchmark or unstable environment.
- Trend charts show each benchmark over time (one line per version).
- Sidebar links let you open older weekly snapshot pages directly.

## CI and Pages
Workflow: `.github/workflows/benchmark-pages.yml`

Triggers:
- Weekly schedule (Monday, 03:17 UTC).
- Manual `workflow_dispatch` with optional inputs:
  - `versions` (comma/space-separated list),
  - `includePrereleaseLatestMajor` (include latest-major alpha/beta/rc in auto version selection),
  - `iterations`,
  - `iterationTimeMs`,
  - `forks`,
  - `threads`.

Manual dispatch from local machine:
```sh
./run-github-weekly-benchmark.sh --watch
```

Windows PowerShell:
```powershell
./run-github-weekly-benchmark.ps1 -Watch
```

Equivalent direct `gh` command:
```sh
gh workflow run .github/workflows/benchmark-pages.yml \
  --repo javalin/javalin-performance-tests-testing \
  --ref main \
  -f iterations=10 \
  -f iterationTimeMs=1000 \
  -f forks=2 \
  -f threads=4
```

Default workflow values:
- `iterations=10`
- `iterationTimeMs=1000`
- `forks=2`
- `threads=4`

These are production-oriented defaults for statistical stability on weekly runs.

Design notes:
- Versions run sequentially in the same job/runner per workflow run (reduces cross-runner noise for comparisons).
- Runner metadata is captured each run (`runner-info.json`).
- Raw benchmark history is stored on branch `benchmark-data` under `runs/<run-id>/`.
- Static report page is generated from history and deployed to GitHub Pages.
- History is append-only: each run gets a unique run id and is added under a new `runs/<run-id>/` folder.

Default scheduled versions are auto-resolved from Maven Central on every run:
- include the latest patch from the latest 3 minors in each of the latest 2 major lines,
- do not include older major lines by default,
- minimum stable cutoff `>= 1.0.0`,
- exclude prereleases unless `includePrereleaseLatestMajor=true`.

Fallback static list is `config/versions.txt`.
You can refresh the fallback files with:
```sh
python3 scripts/update_versions_from_maven.py --output config/versions.txt --minimum 1.0.0 --include-all-latest-majors 2 --latest-minors-per-major 3 --no-include-latest-per-major
python3 scripts/update_versions_from_maven.py --output config/versions-prerelease.txt --minimum 1.0.0 --include-all-latest-majors 2 --latest-minors-per-major 3 --no-include-latest-per-major --include-prerelease-latest-major
```

## PR benchmarks
Workflow: `.github/workflows/benchmark-pr.yml`

Triggers:
- On every pull request.
- Manual `workflow_dispatch` with optional versions and tuning overrides.

Defaults:
- versions from `config/pr-versions.txt`,
- `iterations=10`, `iterationTimeMs=1000`, `forks=2`, `threads=4`.

Output:
- uploads raw benchmark JSON + generated trend report as workflow artifact,
- adds a markdown benchmark summary table to the job summary.

The generated website also includes a plain-language “How To Read This” section.
