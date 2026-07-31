$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot ".dev-pids.json"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output "No CiteMind development PID file found."
    exit 0
}

$pids = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
if ($pids.projectRoot -ne $projectRoot) {
    throw "PID file does not belong to this workspace."
}

foreach ($processId in @($pids.api, $pids.web)) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -in @("python", "node", "pnpm")) {
        Stop-Process -Id $processId
    }
}

Remove-Item -LiteralPath $pidFile
Write-Output "CiteMind development services stopped."
