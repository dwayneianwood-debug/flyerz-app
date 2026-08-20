# Force-free dev ports (Windows + kill-port). Run from repo root: pwsh -File scripts/kill-dev-ports.ps1
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
npx --yes kill-port 5000 3000 2>$null
if ($LASTEXITCODE -ne 0) {
  foreach ($p in 5000, 3000) {
    Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |
      ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  }
}
