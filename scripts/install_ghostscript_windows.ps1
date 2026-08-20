#Requires -Version 5.1
<#
.SYNOPSIS
  Download official Ghostscript Windows x64 installer from Artifex GitHub releases,
  silent-install, add bin to User PATH, verify with gswin64c -v.
#>
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$gsExe = Get-ChildItem -Path 'C:\Program Files\gs' -Recurse -Filter 'gswin64c.exe' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$api = 'https://api.github.com/repos/ArtifexSoftware/ghostpdl-downloads/releases/latest'
$headers = @{ 'User-Agent' = 'Flyerz-Ghostscript-InstallScript' }

if (-not $gsExe) {
  Write-Host '[1/5] Resolving latest official Windows x64 installer from GitHub (Artifex ghostpdl-downloads)...'
  $release = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 120
  $asset = $release.assets | Where-Object { $_.name -match '^gs\d+w64\.exe$' } | Select-Object -First 1
  if (-not $asset) {
    throw 'No gs*w64.exe asset found on latest release (expected official 64-bit Windows installer).'
  }
  $url = $asset.browser_download_url
  $outName = $asset.name
  $dl = Join-Path $env:TEMP $outName
  Write-Host "      Installer: $outName"
  Write-Host "      URL: $url"

  Write-Host '[2/5] Downloading installer...'
  if (Test-Path $dl) { Remove-Item -LiteralPath $dl -Force }
  Invoke-WebRequest -Uri $url -OutFile $dl -UseBasicParsing -TimeoutSec 600
  if (-not (Test-Path $dl) -or ((Get-Item $dl).Length -lt 1MB)) {
    throw "Download failed or file too small: $dl"
  }

  Write-Host '[3/5] Running silent install (/S) (detached; waiting for binaries to appear)...'
  # NSIS wrapper may not exit until child installers finish; do not block on the parent process.
  Start-Process -FilePath $dl -ArgumentList '/S' | Out-Null

  $deadline = (Get-Date).AddMinutes(25)
  while ((Get-Date) -lt $deadline) {
    $gsExe = Get-ChildItem -Path 'C:\Program Files\gs' -Recurse -Filter 'gswin64c.exe' -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($gsExe) { break }
    Start-Sleep -Seconds 3
  }
} else {
  Write-Host '[1-3/5] Ghostscript already installed; skipping download and installer.'
}

Write-Host '[4/5] Locating gswin64c.exe under Program Files\gs\...'
if (-not $gsExe) {
  throw 'gswin64c.exe not found under C:\Program Files\gs after install (timed out). Try running this script elevated (Run as administrator).'
}
$binDir = $gsExe.DirectoryName
Write-Host "      Found: $($gsExe.FullName)"

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @()
if (-not [string]::IsNullOrWhiteSpace($userPath)) {
  $parts = $userPath.Split(';', [System.StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { $_.TrimEnd('\') }
}
$normBin = $binDir.TrimEnd('\')
$already = $false
foreach ($p in $parts) {
  if ([string]::Equals($p, $normBin, [System.StringComparison]::OrdinalIgnoreCase)) { $already = $true; break }
}
if (-not $already) {
  $newUserPath = if ($parts.Count -eq 0) { $normBin } else { ($parts + $normBin) -join ';' }
  [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
  Write-Host "      Appended to User PATH: $normBin"
} else {
  Write-Host '      Bin directory already present on User PATH; skipping.'
}

# Refresh PATH in this session (Machine + User)
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath2 = [Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = "$machinePath;$userPath2"

Write-Host '[5/5] Verifying Ghostscript...'
& (Join-Path $binDir 'gswin64c.exe') -v

Write-Host 'Done.'
