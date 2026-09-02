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
$webExecutable = $null
$webArguments = @()
$pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
$corepack = Get-Command corepack -ErrorAction SilentlyContinue
$node = Get-Command node -ErrorAction SilentlyContinue
$nextCli = Join-Path $webRoot "node_modules\next\dist\bin\next"
$codexNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$codexPnpm = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

if ($pnpm) {
    $webExecutable = $pnpm.Source
    $webArguments = @("dev", "--hostname", "127.0.0.1", "--port", "3000")
} elseif ($corepack) {
    $webExecutable = $corepack.Source
    $webArguments = @("pnpm", "dev", "--hostname", "127.0.0.1", "--port", "3000")
} elseif ($node -and (Test-Path -LiteralPath $nextCli)) {
    $webExecutable = $node.Source
    $webArguments = @($nextCli, "dev", "--hostname", "127.0.0.1", "--port", "3000")
} elseif ((Test-Path -LiteralPath $codexNode) -and (Test-Path -LiteralPath $nextCli)) {
    $webExecutable = $codexNode
    $webArguments = @($nextCli, "dev", "--hostname", "127.0.0.1", "--port", "3000")
} elseif (Test-Path -LiteralPath $codexPnpm) {
    $webExecutable = $codexPnpm
    $webArguments = @("dev", "--hostname", "127.0.0.1", "--port", "3000")
} else {
    throw "Node.js/pnpm not found. Install Node.js 20+ and run: corepack enable"
}

$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $apiRoot -WindowStyle Hidden -PassThru
$webProcess = Start-Process -FilePath $webExecutable -ArgumentList $webArguments -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru

@{
    api = $apiProcess.Id
    web = $webProcess.Id
    projectRoot = $projectRoot
} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8

Write-Output "CiteMind started:"
Write-Output "  Web: http://127.0.0.1:3000"
Write-Output "  API: http://127.0.0.1:8000/docs"
Write-Output "Stop with: ./scripts/stop-dev.ps1"
