#requires -Version 5.1
<#
.SYNOPSIS
  Serve the local update channel on http://127.0.0.1:8899.

.DESCRIPTION
  The desktop app (Electron) asks http://127.0.0.1:8899/latest.yml for the newest
  release (the `publish` setting in `surfaces\gui\package.json`). This serves
  `packaging\dist\updates` - the folder `packaging\release_windows.ps1` publishes into - with
  Python's static server, so an installed build can read the manifest, download the installer,
  check it against the sha512 the manifest names, and update itself.

  Run it while you want updates to be discoverable; Ctrl+C stops it. While it is down a check
  simply finds nothing. A public HTTPS host becomes the channel when one exists.
#>
param(
    [int]$Port = 8899
)
$ErrorActionPreference = "Stop"

$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root    = Split-Path -Parent $Here
$Updates = Join-Path $Root "packaging\dist\updates"
$Python  = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path (Join-Path $Updates "latest.yml"))) {
    Write-Warning "no latest.yml in $Updates yet - run .\packaging\release_windows.ps1 first"
}
Write-Host "Serving $Updates on http://127.0.0.1:$Port (Ctrl+C to stop)" -ForegroundColor Cyan
& $Python -m http.server $Port --bind 127.0.0.1 --directory $Updates
