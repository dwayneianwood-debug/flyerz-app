#Requires -Version 5.1
<#
.SYNOPSIS
  Start Flyerz in production mode for the office PC.

.DESCRIPTION
  Loads HOST and PORT from .env (an existing environment variable wins).
  Turns LAN mode on, builds the client and server only when source files are
  newer than dist (or when -Build is passed), then runs npm start.

.PARAMETER Build
  Rebuild even when dist is already up to date.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\start_prod.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\start_prod.ps1 -Build
#>
param(
    [switch] $Build
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step {
    param([string] $Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Import-DotEnv {
    param([string] $EnvFile)
    if (-not (Test-Path -LiteralPath $EnvFile)) { return }
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return }
        $idx = $line.IndexOf('=')
        if ($idx -lt 1) { return }
        $name = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name -and -not (Test-Path -LiteralPath "Env:$name")) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Stop-ListenersOnPort {
    param([int] $Port)
    $killed = @{}
    try {
        $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            $owningPid = $c.OwningProcess
            if ($owningPid -and $owningPid -gt 4 -and -not $killed.ContainsKey($owningPid)) {
                Stop-Process -Id $owningPid -Force -ErrorAction SilentlyContinue
                $killed[$owningPid] = $true
                Write-Host ('  Port {0} : stopped PID {1}' -f $Port, $owningPid) -ForegroundColor Yellow
            }
        }
    }
    catch {
    }
    $rx = '^.*:' + $Port + '\s+.*LISTENING\s+(\d+)\s*$'
    netstat -ano 2>$null | Select-String -Pattern $rx | ForEach-Object {
        if ($_.Line -match 'LISTENING\s+(\d+)\s*$') {
            $procId = [int]$Matches[1]
            if ($procId -gt 4 -and -not $killed.ContainsKey($procId)) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $killed[$procId] = $true
                Write-Host ('  Port {0} (netstat): stopped PID {1}' -f $Port, $procId) -ForegroundColor Yellow
            }
        }
    }
}

function Test-NeedsBuild {
    if ($Build) { return $true }
    $serverOut = Join-Path $Root 'dist\index.cjs'
    $clientOut = Join-Path $Root 'dist\public\index.html'
    if (-not (Test-Path -LiteralPath $serverOut) -or -not (Test-Path -LiteralPath $clientOut)) {
        return $true
    }
    $built = (Get-Item -LiteralPath $serverOut).LastWriteTimeUtc
    $extensions = @('.ts', '.tsx', '.js', '.jsx', '.css', '.html', '.json')
    foreach ($rel in @('client', 'server', 'shared', 'script')) {
        $dir = Join-Path $Root $rel
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        $newer = Get-ChildItem -LiteralPath $dir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $extensions -contains $_.Extension.ToLower() -and
                $_.FullName -notmatch '\\node_modules\\|\\dist\\' -and
                $_.LastWriteTimeUtc -gt $built
            } |
            Select-Object -First 1
        if ($newer) { return $true }
    }
    foreach ($extra in @('package.json', 'vite.config.ts')) {
        $item = Get-Item -LiteralPath (Join-Path $Root $extra) -ErrorAction SilentlyContinue
        if ($item -and $item.LastWriteTimeUtc -gt $built) { return $true }
    }
    return $false
}

Write-Host ""
Write-Host "Flyerz start_prod.ps1 - production server" -ForegroundColor Magenta
Write-Host "Root: $Root"

Import-DotEnv -EnvFile (Join-Path $Root '.env')
# LAN sharing is on for the office. HOST and PORT still come from .env when set.
$env:LAN_ONLY_MODE = 'true'
$env:NODE_ENV = 'production'

$listenPort = 5000
if ($env:PORT -and $env:PORT.Trim() -ne '') {
    $listenPort = [int]$env:PORT
}

Write-Step "Free port $listenPort"
Stop-ListenersOnPort -Port $listenPort

if (Test-NeedsBuild) {
    Write-Step "Build - source is newer than dist, or -Build was passed"
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    Write-Step "Build - dist is up to date (pass -Build to rebuild)"
}

$shownHost = if ($env:HOST -and $env:HOST.Trim() -ne '') { $env:HOST } else { '0.0.0.0' }
Write-Step "Boot - production server (HOST=$shownHost PORT=$listenPort LAN_ONLY_MODE=true)"
Write-Host "  Open: http://localhost:$listenPort/" -ForegroundColor Green
Write-Host ""

npm start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
