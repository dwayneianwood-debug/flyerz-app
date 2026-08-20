#Requires -Version 5.1
<#
.SYNOPSIS
  Flyerz local dev — nuke ports/processes, clear caches, boot unified Express+Vite (npm run dev).

.NOTES
  This app does NOT run a separate Python HTTP server: root main.py is a stub.
  Backend + frontend dev = single process: npm run dev (tsx server/index.ts + Vite middleware).
  If your IDE depends on Node (Cursor/VS Code), run this from an external PowerShell window,
  or expect extension hosts to restart when global node.exe is killed.

.PARAMETER SkipGlobalKill
  Only kill listeners on ports 3000/5000/5173; do not taskkill all node/python/ghostscript.

.PARAMETER NoBoot
  Cleanup only; do not run npm run dev.
#>
param(
    [switch] $SkipGlobalKill,
    [switch] $NoBoot
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step {
    param([string] $Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Stop-ListenersOnPort {
    param([int] $Port)
    $killed = @{}
    # Prefer Get-NetTCPConnection (Windows 8+ / Server 2012+)
    try {
        $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            # Do not use $pid — it is the automatic "current process id" variable in PowerShell
            $owningPid = $c.OwningProcess
            if ($owningPid -and $owningPid -gt 4 -and -not $killed.ContainsKey($owningPid)) {
                Stop-Process -Id $owningPid -Force -ErrorAction SilentlyContinue
                $killed[$owningPid] = $true
                Write-Host ('  Port {0} : stopped PID {1}' -f $Port, $owningPid) -ForegroundColor Yellow
            }
        }
    }
    catch {
        # Older systems or restricted profiles
    }
    # Fallback: parse netstat -ano for LISTENING + LocalPort
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

function Clear-ProjectDirContents {
    param([string] $RelativePath)
    $full = Join-Path $Root $RelativePath
    if (Test-Path $full) {
        Get-ChildItem -LiteralPath $full -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Cleared: $RelativePath" -ForegroundColor Green
    }
    else {
        New-Item -ItemType Directory -Path $full -Force | Out-Null
        Write-Host "  Created empty: $RelativePath" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "Flyerz start_dev.ps1 - nuke, purge, cache, boot" -ForegroundColor Magenta
Write-Host "Root: $Root"

# --- The Nuke: ports ---------------------------------------------------------
Write-Step "The Nuke - free ports 3000, 5000 (and 5173 for stray Vite)"
foreach ($p in @(3000, 5000, 5173)) {
    Stop-ListenersOnPort -Port $p
}

# --- Process purge -----------------------------------------------------------
if (-not $SkipGlobalKill) {
    Write-Step "Process purge - node, python, Ghostscript CLI"
    $names = @('node', 'python', 'pythonw', 'gswin64c', 'gswin32c')
    foreach ($n in $names) {
        Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "  Stopping $($_.ProcessName) PID $($_.Id)" -ForegroundColor Yellow
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
else {
    Write-Step "Process purge - skipped (SkipGlobalKill)"
}

# --- Cache clear -------------------------------------------------------------
Write-Step "Cache clear - server/temp, server/output, dist, .next"
Clear-ProjectDirContents "server\temp"
Clear-ProjectDirContents "server\output"
Clear-ProjectDirContents "dist"
Clear-ProjectDirContents ".next"

# --- Boot --------------------------------------------------------------------
if ($NoBoot) {
    Write-Step "Boot - skipped (NoBoot). Done."
    exit 0
}

Write-Step "Boot - npm run dev (Express API + Vite on one port, default 5000)"
Write-Host '  Open: http://localhost:5000/  (set PORT in .env to change)' -ForegroundColor Green
Write-Host ""

npm run dev
