[CmdletBinding()]
param(
    [string]$Repo = "",
    [string]$Ref = "",
    [string]$Versions = "",
    [switch]$IncludePrereleaseLatestMajor,
    [int]$Iterations = 10,
    [int]$IterationTimeMs = 1000,
    [int]$Forks = 2,
    [int]$Threads = 4,
    [switch]$Watch,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workflowFile = ".github/workflows/benchmark-pages.yml"

function Show-Usage {
    @"
Usage: ./run-github-weekly-benchmark.ps1 [options]

Dispatches the weekly benchmark workflow on GitHub Actions using workflow_dispatch.

Options:
  -Repo <owner/repo>                  GitHub repository (default: derived from git remote)
  -Ref <branch-or-tag>                Git ref to run on (default: current branch)
  -Versions "<v1,v2 ...>"             Optional explicit versions override
  -IncludePrereleaseLatestMajor       Include alpha/beta/rc from latest major
  -Iterations <n>                     JMH iterations (default: 10)
  -IterationTimeMs <n>                Iteration time in ms (default: 1000)
  -Forks <n>                          JMH forks (default: 2)
  -Threads <n>                        JMH threads (default: 4)
  -Watch                              Watch run progress after dispatch
  -Help                               Show this help
"@
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Derive-Repo {
    $remote = & git config --get remote.origin.url 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remote)) {
        throw "Could not derive repository from git remote. Use -Repo owner/name."
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

function Derive-Ref {
    $branch = & git rev-parse --abbrev-ref HEAD 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
        return "main"
    }
    return $branch.Trim()
}

if ($Help) {
    Show-Usage
    exit 0
}

Require-Command gh
Require-Command git

& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

if ([string]::IsNullOrWhiteSpace($Repo)) {
    $Repo = Derive-Repo
}
if ([string]::IsNullOrWhiteSpace($Ref)) {
    $Ref = Derive-Ref
}

$dispatchArgs = @(
    "workflow", "run", $workflowFile,
    "--repo", $Repo,
    "--ref", $Ref,
    "-f", "iterations=$Iterations",
    "-f", "iterationTimeMs=$IterationTimeMs",
    "-f", "forks=$Forks",
    "-f", "threads=$Threads"
)
if (-not [string]::IsNullOrWhiteSpace($Versions)) {
    $dispatchArgs += @("-f", "versions=$Versions")
}
if ($IncludePrereleaseLatestMajor.IsPresent) {
    $dispatchArgs += @("-f", "includePrereleaseLatestMajor=true")
}

Write-Host "Dispatching $workflowFile on $Repo@$Ref"
& gh @dispatchArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to dispatch workflow"
}

Start-Sleep -Seconds 2
$runUrl = & gh run list --repo $Repo --workflow $workflowFile --branch $Ref --limit 1 --json url --jq '.[0].url // ""' 2>$null
$runId = & gh run list --repo $Repo --workflow $workflowFile --branch $Ref --limit 1 --json databaseId --jq '.[0].databaseId // ""' 2>$null

if (-not [string]::IsNullOrWhiteSpace($runUrl)) {
    Write-Host "Run URL: $runUrl"
} else {
    Write-Host "Workflow dispatched. Open runs: https://github.com/$Repo/actions/workflows/benchmark-pages.yml"
}

if ($Watch.IsPresent -and -not [string]::IsNullOrWhiteSpace($runId)) {
    & gh run watch $runId --repo $Repo
}
