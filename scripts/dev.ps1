#requires -Version 5.1
<#
  Launches the backend (uvicorn) and the frontend (vite) in parallel.
  Press Ctrl+C in either child window to stop that process.
#>
$ErrorActionPreference = 'Stop'

$repoRoot   = Resolve-Path (Join-Path $PSScriptRoot '..')
$backendDir = Join-Path $repoRoot 'backend'
$frontDir   = Join-Path $repoRoot 'frontend'

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return $listener.LocalEndpoint.Port
    }
    finally {
        $listener.Stop()
    }
}

if (-not (Test-Path (Join-Path $backendDir 'pyproject.toml'))) {
    throw "Cannot find backend/pyproject.toml at $backendDir"
}
if (-not (Test-Path (Join-Path $frontDir 'package.json'))) {
    throw "Cannot find frontend/package.json at $frontDir"
}

$venvActivate = Join-Path $backendDir '.venv\Scripts\Activate.ps1'
if (-not (Test-Path $venvActivate)) {
    Write-Host "[dev] No backend venv detected at $venvActivate" -ForegroundColor Yellow
    Write-Host "[dev] Create one with:  python -m venv backend\.venv ;  backend\.venv\Scripts\Activate.ps1 ;  pip install -e backend[dev]" -ForegroundColor Yellow
}

$nodeModules = Join-Path $frontDir 'node_modules'
if (-not (Test-Path $nodeModules)) {
    Write-Host "[dev] frontend/node_modules not found. Running 'npm install' first..." -ForegroundColor Yellow
    Push-Location $frontDir
    try { npm install } finally { Pop-Location }
}

$backendPort = if ($env:WMESM_BACKEND_PORT) { [int]$env:WMESM_BACKEND_PORT } else { Get-FreeTcpPort }
$frontendPort = if ($env:WMESM_FRONTEND_PORT) { [int]$env:WMESM_FRONTEND_PORT } else { Get-FreeTcpPort }

Write-Host "[dev] Starting backend on http://localhost:$backendPort" -ForegroundColor Green
$backendCmd = "if (Test-Path '$venvActivate') { . '$venvActivate' } ; `$env:WMESM_BACKEND_PORT='$backendPort' ; uvicorn app.main:app --reload --port $backendPort"
Start-Process -FilePath 'pwsh' -ArgumentList @('-NoExit', '-Command', $backendCmd) -WorkingDirectory $backendDir | Out-Null

Write-Host "[dev] Starting frontend on http://localhost:$frontendPort" -ForegroundColor Green
$frontendCmd = "`$env:WMESM_BACKEND_PORT='$backendPort' ; `$env:WMESM_FRONTEND_PORT='$frontendPort' ; npm run dev -- --host localhost"
Start-Process -FilePath 'pwsh' -ArgumentList @('-NoExit', '-Command', $frontendCmd) -WorkingDirectory $frontDir | Out-Null

Write-Host "[dev] Two windows launched. Close them with Ctrl+C inside each window." -ForegroundColor Green
