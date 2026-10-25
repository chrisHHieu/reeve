#requires -Version 5.1
<#
.SYNOPSIS
  Publish a Windows release to the LOCAL update channel (127.0.0.1:8899).

.DESCRIPTION
  The desktop app (Electron) asks its update channel for `latest.yml`, which names the
  newest installer with its sha512; electron-updater checks the downloaded file against it before
  installing. This script:

    1. builds the installer (build_windows.ps1), unless -SkipBuild;
    2. copies `latest.yml`, `Reeve-Setup-<version>.exe` and its `.blockmap` (for differential
       downloads) into `packaging\dist\updates\`;
    3. leaves the folder served by `scripts\update_channel.ps1` at http://127.0.0.1:8899/.

  Publishing a version the app should SEE means a higher `version` in
  `surfaces\gui\package.json` than what is installed: the app compares versions, so publishing
  the installed one again is a no-op.

.EXAMPLE
  .\packaging\release_windows.ps1                 # build, stage
  .\packaging\release_windows.ps1 -SkipBuild      # reuse the last build under surfaces\gui\release
  .\scripts\update_channel.ps1                    # serve dist\updates on :8899
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    # The largest installer this publishes (a publish-time cap, moved here from the Tauri
    # manifest script). 400 MiB, not 250: the installer now carries Chromium beside the ~420 MB sidecar.
    # Raising it is a reviewed edit, not an accident of the build.
    [int]$MaxMiB = 400
)
$ErrorActionPreference = "Stop"

$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Platform = Split-Path -Parent $Here
$Package  = Join-Path $Platform "surfaces\gui\package.json"
$Release  = Join-Path $Platform "surfaces\gui\release"
$Updates  = Join-Path $Here "dist\updates"

$Version = (Get-Content $Package -Raw | ConvertFrom-Json).version
Write-Host "==> releasing Reeve $Version to the local update channel" -ForegroundColor Cyan

if (-not $SkipBuild) {
    & (Join-Path $Here "build_windows.ps1")
} else {
    Write-Host "    -SkipBuild: reusing the installer under $Release" -ForegroundColor DarkGray
}

$Setup = Join-Path $Release "Reeve-Setup-$Version.exe"
$Manifest = Join-Path $Release "latest.yml"
if (-not (Test-Path $Setup)) { throw "no installer for $Version at $Setup" }
if (-not (Test-Path $Manifest)) { throw "no latest.yml beside the installer: electron-builder writes it with the publish setting in package.json" }
if (-not (Select-String -Path $Manifest -SimpleMatch "version: $Version" -Quiet)) { throw "latest.yml does not name $Version" }
$SizeMiB = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
if ($SizeMiB -gt $MaxMiB) { throw "the installer is $SizeMiB MiB, over the $MaxMiB MiB cap: nothing is published (raise -MaxMiB only on purpose)" }
Write-Host "    installer: $SizeMiB MiB (cap $MaxMiB MiB)"

New-Item -ItemType Directory -Force -Path $Updates | Out-Null
Copy-Item -Force $Setup $Updates
Copy-Item -Force $Manifest $Updates
$Blockmap = "$Setup.blockmap"
if (Test-Path $Blockmap) { Copy-Item -Force $Blockmap $Updates }

Write-Host ""
Write-Host "Published $Version to $Updates" -ForegroundColor Green
Write-Host "Serve it with:  .\scripts\update_channel.ps1" -ForegroundColor Green
