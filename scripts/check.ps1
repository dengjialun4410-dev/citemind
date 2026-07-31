param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $projectRoot "apps\api"
$webRoot = Join-Path $projectRoot "apps\web"
$pythonExe = Join-Path $apiRoot ".venv\Scripts\python.exe"

if (-not $SkipBackend) {
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw "Backend virtual environment not found. Create apps/api/.venv and install requirements.txt first."
    }
    Push-Location $apiRoot
    try {
        & $pythonExe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    }
    finally { Pop-Location }
}

if (-not $SkipFrontend) {
    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        throw "pnpm not found. Install Node.js and run: corepack enable"
    }
    Push-Location $webRoot
    $previousCI = $env:CI
    try {
        $env:CI = "true"
        & pnpm build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    }
    finally {
        $env:CI = $previousCI
        Pop-Location
    }
}

Write-Output "CiteMind checks passed."
