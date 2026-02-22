[CmdletBinding()]
param(
    [ValidateSet("quick", "weekly")]
    [string]$Mode = "weekly",
    [string]$Versions = "",
    [switch]$IncludePrereleaseLatestMajor,
    [int]$Iterations = 0,
    [int]$IterationTimeMs = 0,
    [int]$Forks = 0,
    [int]$Threads = 0,
    [string]$HistoryRoot = "local-history/runs",
    [string]$SiteDir = "local-history/site",
    [string]$GradleUserHome = ".gradle-local-user-home",
    [string]$GradleProjectCacheDir = ".gradle-local-project-cache",
    [string]$Repository = "",
    [string]$RunId = "",
    [switch]$Serve,
    [int]$Port = 8000,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $script:ScriptDir

function Show-Usage {
    @"
Usage: ./run-local-benchmarks.ps1 [options]

Runs Javalin benchmarks locally, stores a new historical run, and regenerates the static site.

Modes:
  -Mode weekly   Production-like weekly run (default): auto-resolve versions from Maven policy
  -Mode quick    Fast local smoke run: versions from config/pr-versions.txt

Options:
  -Mode <quick|weekly>                  Run preset (default: weekly)
  -Versions "<v1,v2 ...>"               Explicit versions (overrides mode defaults)
  -IncludePrereleaseLatestMajor         Include all alpha/beta/rc from latest major for auto version resolution
  -Iterations <n>                       JMH warmup + measurement iterations
  -IterationTimeMs <n>                  JMH warmup + measurement time in ms
  -Forks <n>                            JMH forks
  -Threads <n>                          JMH worker threads
  -HistoryRoot <dir>                    Directory containing run folders (default: local-history/runs)
  -SiteDir <dir>                        Generated site output dir (default: local-history/site)
  -GradleUserHome <dir>                 Gradle user home dir (default: .gradle-local-user-home)
  -GradleProjectCacheDir <dir>          Gradle project cache dir (default: .gradle-local-project-cache)
  -Repository <name>                    Report repository label (default: derived from git remote)
  -RunId <id>                           Override run id (default: local-UTC-timestamp)
  -Serve                                Start local HTTP server after generation
  -Port <n>                             HTTP server port for -Serve (default: 8000)
  -Help                                 Show this help

Examples:
  ./run-local-benchmarks.ps1
  ./run-local-benchmarks.ps1 -Mode weekly -IncludePrereleaseLatestMajor
  ./run-local-benchmarks.ps1 -Versions "5.6.5 6.7.0" -Iterations 2 -IterationTimeMs 300
"@
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Get-PythonCommand {
    foreach ($candidate in @("python3", "python")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            return $candidate
        }
    }
    throw "Missing required command: python3 or python"
}

function Resolve-UserPath {
    param([string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $script:ScriptDir $PathValue))
}

function Derive-Repository {
    $remote = & git config --get remote.origin.url 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remote)) {
        return (Split-Path -Leaf $script:ScriptDir)
    }
    $value = $remote.Trim()
    if ($value.StartsWith("https://github.com/")) {
        $value = $value.Substring("https://github.com/".Length)
    } elseif ($value.Contains(":")) {
        $parts = $value.Split(":")
        $value = $parts[-1]
    }
    if ($value.EndsWith(".git")) {
        $value = $value.Substring(0, $value.Length - 4)
    }
    return $value
}

function Get-JavaMajor {
    param([string]$JavaExe)
    if (-not (Test-Path $JavaExe)) {
        return 0
    }
    $line = & $JavaExe -version 2>&1 | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($line)) {
        return 0
    }
    if ($line -match '"([^"]+)"') {
        $raw = $Matches[1]
        if ($raw.StartsWith("1.")) {
            $parts = $raw.Split(".")
            if ($parts.Count -ge 2) {
                return [int]$parts[1]
            }
        } else {
            $first = $raw.Split(".")[0]
            return [int]$first
        }
    }
    return 0
}

function Get-JavaHomeFromExe {
    param([string]$JavaExe)
    $binDir = Split-Path -Parent $JavaExe
    return Split-Path -Parent $binDir
}

function Select-CompatibleJdk {
    $candidateJavaExecutables = New-Object System.Collections.Generic.List[string]

    # Prefer explicit JAVA_HOME first.
    if ($env:JAVA_HOME) {
        $candidateJavaExecutables.Add((Join-Path $env:JAVA_HOME "bin/java.exe"))
        $candidateJavaExecutables.Add((Join-Path $env:JAVA_HOME "bin/java"))
    }

    # Then prefer active java on PATH (works well with `mise exec ...`).
    $javaCmd = Get-Command java -ErrorAction SilentlyContinue
    if ($javaCmd) {
        $candidateJavaExecutables.Add($javaCmd.Source)
    }

    if (Get-Command mise -ErrorAction SilentlyContinue) {
        $miseJava = & mise which java 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($miseJava) -and (Test-Path $miseJava)) {
            $candidateJavaExecutables.Add($miseJava.Trim())
        }
    }

    if ($env:ProgramFiles) {
        foreach ($root in @("$env:ProgramFiles\Java", "$env:ProgramFiles\Eclipse Adoptium")) {
            if (Test-Path $root) {
                Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                    $candidateJavaExecutables.Add((Join-Path $_.FullName "bin/java.exe"))
                }
            }
        }
    }

    foreach ($linuxPath in @(
        "/usr/lib/jvm/java-21-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-17-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-21-openjdk/bin/java",
        "/usr/lib/jvm/java-17-openjdk/bin/java"
    )) {
        $candidateJavaExecutables.Add($linuxPath)
    }

    $unique = $candidateJavaExecutables | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
    foreach ($javaExe in $unique) {
        if (-not (Test-Path $javaExe)) {
            continue
        }
        $major = Get-JavaMajor -JavaExe $javaExe
        if ($major -eq 21 -or $major -eq 17) {
            $javaHome = Get-JavaHomeFromExe -JavaExe $javaExe
            $env:JAVA_HOME = $javaHome
            $env:PATH = "$javaHome$([System.IO.Path]::DirectorySeparatorChar)bin$([System.IO.Path]::PathSeparator)$($env:PATH)"
            return
        }
    }

    throw "Could not find a compatible local JDK (21 or 17). Install with 'mise install' or install JDK 21/17 system-wide."
}

function Ensure-NoRunningJmh {
    try {
        $javaProcesses = Get-Process -Name java -ErrorAction SilentlyContinue
        if (-not $javaProcesses) {
            return
        }
        $running = @()
        foreach ($process in $javaProcesses) {
            $cmd = ""
            try {
                $cmd = $process.Path
            } catch {}
            if ([string]::IsNullOrWhiteSpace($cmd)) {
                continue
            }
            if ($cmd -match "java") {
                # Best-effort: when CommandLine is available, use it.
                try {
                    $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction SilentlyContinue).CommandLine
                    if ($commandLine -and $commandLine -match "org\.openjdk\.jmh\.runner\.ForkedMain") {
                        $running += "$($process.Id): $commandLine"
                    }
                } catch {}
            }
        }
        if ($running.Count -gt 0) {
            throw ("Another JMH benchmark process is already running:`n" + ($running -join "`n") + "`nStop it before starting a new run.")
        }
    } catch {
        throw $_
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

Require-Command git
$python = Get-PythonCommand

if (-not (Test-Path ".\gradlew") -and -not (Test-Path ".\gradlew.bat")) {
    throw "Missing Gradle wrapper (gradlew/gradlew.bat) at repository root"
}

Ensure-NoRunningJmh
Select-CompatibleJdk

if ($Iterations -le 0) {
    $Iterations = if ($Mode -eq "weekly") { 10 } else { 2 }
}
if ($IterationTimeMs -le 0) {
    $IterationTimeMs = if ($Mode -eq "weekly") { 1000 } else { 300 }
}
if ($Forks -le 0) {
    $Forks = if ($Mode -eq "weekly") { 2 } else { 1 }
}
if ($Threads -le 0) {
    $Threads = 4
}

if ([string]::IsNullOrWhiteSpace($Repository)) {
    $Repository = Derive-Repository
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $RunId = "local-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
}
$runTimestampUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$historyRootAbs = Resolve-UserPath -PathValue $HistoryRoot
$siteDirAbs = Resolve-UserPath -PathValue $SiteDir
$gradleUserHomeAbs = Resolve-UserPath -PathValue $GradleUserHome
$gradleProjectCacheDirAbs = Resolve-UserPath -PathValue $GradleProjectCacheDir
$runDir = Join-Path $historyRootAbs $RunId

if (Test-Path $runDir) {
    throw "Run directory already exists: $runDir. Use -RunId with a unique value."
}
New-Item -ItemType Directory -Force -Path (Join-Path $runDir "results") | Out-Null
New-Item -ItemType Directory -Force -Path $gradleUserHomeAbs | Out-Null
New-Item -ItemType Directory -Force -Path $gradleProjectCacheDirAbs | Out-Null
$env:GRADLE_USER_HOME = $gradleUserHomeAbs

$versionsJson = ""
if (-not [string]::IsNullOrWhiteSpace($Versions)) {
    $versionsJson = & $python scripts/resolve_versions.py --raw $Versions --config config/versions.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to resolve explicit versions"
    }
} else {
    if ($Mode -eq "weekly") {
        $autoArgs = @(
            "scripts/update_versions_from_maven.py",
            "--output", (Join-Path $env:TEMP "local-auto-versions.txt"),
            "--minimum", "1.0.0",
            "--include-all-latest-majors", "2",
            "--latest-minors-per-major", "3",
            "--no-include-latest-per-major",
            "--latest-prerelease-count", "2",
            "--include-latest-snapshot",
            "--json"
        )
        if ($IncludePrereleaseLatestMajor.IsPresent) {
            $autoArgs += "--include-prerelease-latest-major"
        }

        $versionsJson = & $python @autoArgs
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionsJson) -or $versionsJson.Trim() -eq "[]") {
            Write-Host "Auto version resolution failed; falling back to config/versions.txt"
            $versionsJson = & $python scripts/resolve_versions.py --config config/versions.txt
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to resolve versions from fallback config"
            }
        }
    } else {
        $versionsJson = & $python scripts/resolve_versions.py --config config/pr-versions.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to resolve versions from quick-mode config"
        }
    }
}

$tmpVersionsFile = [System.IO.Path]::GetTempFileName()
try {
    & $python scripts/json_to_lines.py $versionsJson | Set-Content -Path $tmpVersionsFile -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to expand version list"
    }

    $versionLines = Get-Content -Path $tmpVersionsFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
    $versionCount = $versionLines.Count

    Write-Host "== Local Benchmark Run =="
    Write-Host "mode=$Mode"
    Write-Host "runId=$RunId"
    Write-Host "runTimestampUtc=$runTimestampUtc"
    Write-Host "repository=$Repository"
    Write-Host "versions=$versionCount"
    Write-Host "iterations=$Iterations iterationTimeMs=$IterationTimeMs forks=$Forks threads=$Threads"
    Write-Host "historyRoot=$historyRootAbs"
    Write-Host "siteDir=$siteDirAbs"
    Write-Host "gradleUserHome=$gradleUserHomeAbs"
    Write-Host "gradleProjectCacheDir=$gradleProjectCacheDirAbs"
    Write-Host "javaHome=$env:JAVA_HOME"
    $javaVersionLine = & (Join-Path $env:JAVA_HOME "bin/java") -version 2>&1 | Select-Object -First 1
    Write-Host "javaVersion=$javaVersionLine"
    Write-Host ""

    & $python scripts/collect_runner_info.py (Join-Path $runDir "runner-info.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to collect runner info"
    }

    $gitSha = (& git rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitSha)) {
        $gitSha = "local"
    }
    $gitRef = (& git rev-parse --abbrev-ref HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitRef)) {
        $gitRef = "local"
    }

    & $python scripts/write_run_metadata.py `
        --output (Join-Path $runDir "run-metadata.json") `
        --run-id $RunId `
        --run-timestamp-utc $runTimestampUtc `
        --versions-json $versionsJson `
        --iterations "$Iterations" `
        --iteration-time-ms "$IterationTimeMs" `
        --forks "$Forks" `
        --threads "$Threads" `
        --repository $Repository `
        --workflow "local-script" `
        --run-number "0" `
        --run-attempt "1" `
        --git-sha $gitSha.Trim() `
        --git-ref $gitRef.Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to write run metadata"
    }

    $gradleCmd = if (Test-Path ".\gradlew.bat") { ".\gradlew.bat" } else { ".\gradlew" }
    foreach ($version in $versionLines) {
        Write-Host ">> Running benchmark for Javalin $version"
        & $gradleCmd --no-daemon clean benchmark `
            --project-cache-dir $gradleProjectCacheDirAbs `
            "-PjavalinVersion=$version" `
            "-Piterations=$Iterations" `
            "-PiterationTime=$IterationTimeMs" `
            "-Pthreads=$Threads" `
            "-Pforks=$Forks" `
            "-PresultFormat=json"
        if ($LASTEXITCODE -ne 0) {
            throw "Gradle benchmark failed for version $version"
        }

        $src = Join-Path $script:ScriptDir ("results/{0}.json" -f $version)
        $dst = Join-Path $runDir ("results/{0}.json" -f $version)
        Copy-Item -Path $src -Destination $dst -Force
    }

    New-Item -ItemType Directory -Force -Path $siteDirAbs | Out-Null
    & $python scripts/generate_pages.py --history-root $historyRootAbs --output-dir $siteDirAbs --repository $Repository
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to generate static site"
    }

    $summary = Get-Content -Path (Join-Path $siteDirAbs "summary.json") -Raw | ConvertFrom-Json
    Write-Host ""
    Write-Host "== Report Updated =="
    Write-Host "latestRunId: $($summary.latestRunId)"
    Write-Host "benchmarks: $($summary.rows.Count)"
    $sampleSet = @($summary.rows | ForEach-Object { $_.samples } | Select-Object -Unique | Sort-Object)
    Write-Host "distinctSamples: [$($sampleSet -join ', ')]"
    Write-Host "index: $(Join-Path $siteDirAbs 'index.html')"

    if ($Serve.IsPresent) {
        Write-Host ""
        Write-Host "Serving site at http://localhost:$Port"
        & $python -m http.server $Port --directory $siteDirAbs
    } else {
        Write-Host ""
        Write-Host "Done. Open $(Join-Path $siteDirAbs 'index.html')"
        Write-Host "Or run: $python -m http.server $Port --directory `"$siteDirAbs`""
    }
}
finally {
    if (Test-Path $tmpVersionsFile) {
        Remove-Item -Path $tmpVersionsFile -Force -ErrorAction SilentlyContinue
    }
}
