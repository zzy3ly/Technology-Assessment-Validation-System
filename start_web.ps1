param(
    [int]$Port = 7777,
    [switch]$SkipBuild,
    [switch]$StartSearch,
    [switch]$Background
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv. Install the project Python dependencies first.'
}
$portProbe = New-Object System.Net.Sockets.TcpClient
try {
    try { $null = $portProbe.ConnectAsync('127.0.0.1', $Port).Wait(500) } catch { }
    if ($portProbe.Connected) {
        throw "Port $Port is already in use. Open the running app or choose another -Port."
    }
} finally { $portProbe.Dispose() }
if ($StartSearch) {
    docker compose --profile core up -d redis searxng
    if ($LASTEXITCODE -ne 0) { throw 'Search startup failed. Start Docker Desktop and retry.' }
}
if (-not $SkipBuild) {
    Push-Location 'frontend\agentic-seek-front'
    try {
        if (-not (Test-Path -LiteralPath 'node_modules\react-scripts\bin\react-scripts.js')) {
            npm.cmd ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath 'frontend\agentic-seek-front\build\index.html')) {
    throw 'No frontend build. Run without -SkipBuild.'
}
$env:BACKEND_PORT = "$Port"
$env:BACKEND_HOST = '127.0.0.1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
if ($Background) {
    $logDir = Join-Path $PSScriptRoot '.web-runtime'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $process = Start-Process -FilePath $pythonPath -ArgumentList 'api.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'api.out.log') -RedirectStandardError (Join-Path $logDir 'api.err.log')
    Write-Host "Web process started: PID=$($process.Id). Logs: $logDir"
    Write-Host "Open http://127.0.0.1:$Port after startup."
} else {
    Write-Host "Open http://127.0.0.1:$Port -- Ctrl+C to stop."
    & $pythonPath api.py
}
