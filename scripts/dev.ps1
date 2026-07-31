$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $projectRoot "apps\api"
$webRoot = Join-Path $projectRoot "apps\web"
$pythonExe = Join-Path $apiRoot ".venv\Scripts\python.exe"
$pidFile = Join-Path $projectRoot ".dev-pids.json"

if (Test-Path -LiteralPath $pidFile) {
    throw "A development PID file already exists. Run scripts/stop-dev.ps1 first."
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Backend virtual environment not found. Follow README.md local setup first."
}
$pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
if (-not $pnpm) {
    throw "pnpm not found. Install Node.js and run: corepack enable"
}

$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--reload","--host","127.0.0.1","--port","8000" -WorkingDirectory $apiRoot -WindowStyle Hidden -PassThru
$webProcess = Start-Process -FilePath $pnpm.Source -ArgumentList "dev","--hostname","127.0.0.1","--port","3000" -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru

@{
    api = $apiProcess.Id
    web = $webProcess.Id
    projectRoot = $projectRoot
} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8

Write-Output "CiteMind started:"
Write-Output "  Web: http://127.0.0.1:3000"
Write-Output "  API: http://127.0.0.1:8000/docs"
Write-Output "Stop with: ./scripts/stop-dev.ps1"
