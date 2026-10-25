#requires -Version 5.1
<#
.SYNOPSIS
  The Windows desktop app and its NSIS installer (Electron shell).

.DESCRIPTION
  What build_dmg.sh is on macOS:
    1. PyInstaller freezes the server into one folder (onedir), so no venv is needed at run time.
    2. It is staged at surfaces\gui\binaries\sidecar\ with a portable Node, @playwright/mcp and
       ripgrep.
    3. The dictation host (`reeve-stt-host.exe`, from the stt crate) is built next to it.
    4. vite builds the app and esbuild the shell, then `electron-builder --win nsis` writes
       Reeve-Setup-<version>.exe and latest.yml (what the updater reads) to surfaces\gui\release.

  Needs:
    - Rust (rustup) with the x86_64-pc-windows-msvc target and the MSVC C++ build tools (link.exe)
      for the dictation host. whisper-rs builds with cmake; when none is on PATH, the copy that VS
      Build Tools ships is put there below.
    - Node and npm, for the app and the shell.
    - A venv at .venv in the repository with the package installed editable, and pyinstaller.
      typer is only needed while freezing: PyInstaller imports `mcp.cli`, which calls sys.exit()
      when typer is missing and stops the build. The `computer-use` extra is what freezes the
      reeve-cua desktop sidecar, and the build stops early without it. The `whatsapp` extra
      (neonize) carries the personal WhatsApp connector, a regular one since 2026-10-21; without
      it the app lists the connector and says its library is missing.
        py -m venv .venv ; .\.venv\Scripts\pip install -e ".[bedrock,computer-use,whatsapp]" pyinstaller tzdata typer

  The installer is UNSIGNED: its first run shows SmartScreen ("More info" -> "Run anyway").
  Authenticode signing comes later.

  Experimental connectors are left out (the spec drops harness.integrations.connectors.
  experimental); a build of your own can keep them:
    $env:REEVE_EXPERIMENTAL = "1"; .\build_windows.ps1
#>
[CmdletBinding()]
param(
    # Stage the sidecar and the dictation host, then stop: no app build, no installer.
    [switch]$StageOnly
)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Platform = Split-Path -Parent $Here
$Gui = Join-Path $Platform "surfaces\gui"
$Venv = Join-Path $Platform ".venv"
$PyInstaller = Join-Path $Venv "Scripts\pyinstaller.exe"
$BinDir = Join-Path $Gui "binaries"
$Sidecar = Join-Path $BinDir "sidecar"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Assert-Exit($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what (exit $LASTEXITCODE)" }
}

function Assert-Tool($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$name' not found on PATH. See the prerequisites in this script's header."
    }
}

# A reeve-server.exe still running from the output folder (an earlier smoke test, say) holds the
# exe open and PyInstaller's overwrite fails with Access is denied. Only processes started FROM that
# folder are stopped: matching on the name alone once stopped the installed app's server in the
# middle of a session (2026-10-15).
function Stop-OutputServers {
    $output = Join-Path $Here "dist"
    $holding = Get-Process -Name "reeve-server" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($output, [System.StringComparison]::OrdinalIgnoreCase) }
    if ($holding) {
        Step "stopping $($holding.Count) running reeve-server process(es) holding the output exe"
        $holding | Stop-Process -Force
        Start-Sleep -Seconds 1
    }
}

function Assert-ComputerUse {
    # The spec leaves reeve-cua out when uiautomation is missing, which would ship an installer with
    # no desktop control and no word about it.
    & (Join-Path $Venv "Scripts\python.exe") -c "import uiautomation, mss, pyautogui, pyperclip, PIL"
    if ($LASTEXITCODE -ne 0) {
        throw "computer-use deps missing from the build venv. Run: .\.venv\Scripts\pip install -e `".[computer-use]`""
    }
}

function Invoke-Freeze {
    Step "[1/4] PyInstaller: bundling reeve-server"
    & $PyInstaller --noconfirm --clean --distpath (Join-Path $Here "dist") --workpath (Join-Path $Here "build") (Join-Path $Here "reeve-server.spec")
    Assert-Exit "PyInstaller failed"
}

# The onedir bundle (the exe and _internal\) ships as electron-builder `extraResources`, at
# <install>\resources\sidecar\; onefile unpacked itself on every launch and cost seconds of splash.
function Copy-Sidecar {
    Step "[2/4] staging sidecar resources (+ node runtime + ripgrep + reeve-cua)"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    if (Test-Path $Sidecar) { Remove-Item -Recurse -Force $Sidecar }
    Copy-Item -Recurse -Force (Join-Path $Here "dist\reeve-server") $Sidecar
    Write-Host "    -> $Sidecar"
}

# The browser MCP server (@playwright/mcp) is a Node program and nobody's machine can be assumed to
# have Node, so the installer brings a portable one with the package installed, where
# builtin_mcp_servers() looks beside the sidecar exe:
#   sidecar\node\node.exe                             (the runtime, no installer of its own)
#   sidecar\mcp\node_modules\@playwright\mcp\cli.js   (the server, installed here)
function Add-Node {
    $version = "22.14.0"
    $unpacked = Join-Path $env:TEMP "node-v$version-win-x64"
    $archive = "$unpacked.zip"
    $nodeDir = Join-Path $Sidecar "node"
    $mcpDir = Join-Path $Sidecar "mcp"
    New-Item -ItemType Directory -Force -Path $nodeDir, $mcpDir | Out-Null
    Write-Host "    downloading node v$version (portable)"
    Invoke-WebRequest -Uri "https://nodejs.org/dist/v$version/node-v$version-win-x64.zip" -OutFile $archive
    Expand-Archive -Path $archive -DestinationPath $env:TEMP -Force
    Copy-Item (Join-Path $unpacked "node.exe") $nodeDir
    Remove-Item -Recurse -Force $unpacked
    Remove-Item -Force $archive
    Write-Host "    npm-installing @playwright/mcp into the bundle"
    & npm install --prefix $mcpDir "@playwright/mcp@latest" --no-audit --no-fund
    Assert-Exit "npm install @playwright/mcp failed"
}

# `grep` and `list_files` run ripgrep, which Windows does not have; without it they walk in Python
# (12.7 s against 0.2 s over this repository, 2026-10-11). A pinned build goes where
# grep_engines.carried_rg() looks, sidecar\rg\rg.exe with its licence files, once its archive
# matches the hash the release publishes.
function Add-Ripgrep {
    $version = "15.2.0"
    $expected = "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5"
    $name = "ripgrep-$version-x86_64-pc-windows-msvc"
    $rgDir = Join-Path $Sidecar "rg"
    $archive = Join-Path $env:TEMP "$name.zip"
    New-Item -ItemType Directory -Force -Path $rgDir | Out-Null
    Write-Host "    downloading ripgrep v$version"
    Invoke-WebRequest -Uri "https://github.com/BurntSushi/ripgrep/releases/download/$version/$name.zip" -OutFile $archive
    $got = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash
    if ($got -ne $expected) { throw "ripgrep archive hash mismatch: got $got, expected $expected" }
    Expand-Archive -Path $archive -DestinationPath $env:TEMP -Force
    foreach ($file in "rg.exe", "LICENSE-MIT", "UNLICENSE", "COPYING") {
        Copy-Item (Join-Path $env:TEMP "$name\$file") $rgDir
    }
    Remove-Item -Recurse -Force (Join-Path $env:TEMP $name)
    Remove-Item -Force $archive
    $says = & (Join-Path $rgDir "rg.exe") --version
    Assert-Exit "the staged rg.exe does not run"
    Write-Host "    -> $(@($says)[0])"
}

function Build-DictationHost {
    Step "[3/4] dictation host (stt crate)"
    $cmake = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    if (-not (Get-Command cmake -ErrorAction SilentlyContinue) -and (Test-Path $cmake)) { $env:PATH = "$cmake;$env:PATH" }
    Push-Location (Join-Path $Platform "stt")
    try {
        & cargo build --release --bin reeve-stt-host
        Assert-Exit "the dictation host did not build"
    }
    finally {
        Pop-Location
    }
    $built = Join-Path $BinDir "reeve-stt-host.exe"
    Copy-Item -Force (Join-Path $Platform "stt\target\release\reeve-stt-host.exe") $built
    Write-Host "    -> $built"
}

function Build-Installer {
    Step "[4/4] the app, the shell, and the installer (electron-builder)"
    Push-Location $Gui
    try {
        & npm run build
        Assert-Exit "the app build failed"
        & npm run electron:build
        Assert-Exit "the shell build failed"
        # A tree that carries the connectors' client ids is the private repository's: its build is
        # named apart and never updates from the public feed, which would install a build without
        # them. A public clone has no such file.
        $builder = @("--win", "nsis", "--publish", "never")
        if (Test-Path (Join-Path $Platform "harness\safety\secrets\client_ids.json")) {
            Write-Host "    private build: the connectors' client ids are in, updates are off"
            $builder += @("-c.extraMetadata.reeveChannel=private", '-c.nsis.artifactName=Reeve-Setup-${version}-private.${ext}')
        }
        & npx electron-builder @builder
        Assert-Exit "electron-builder failed"
    }
    finally {
        Pop-Location
    }
}

Assert-Tool cargo
Assert-Tool npm
if (-not (Test-Path $PyInstaller)) {
    throw "PyInstaller not found at $PyInstaller. Create the venv and install deps (see header)."
}
Stop-OutputServers
Assert-ComputerUse
Invoke-Freeze
Copy-Sidecar
Add-Node
Add-Ripgrep
Build-DictationHost
if ($StageOnly) {
    Write-Host "Staged under $BinDir (-StageOnly: no app build, no installer)." -ForegroundColor Green
    return
}
Build-Installer

$Release = Join-Path $Gui "release"
Write-Host ""
Write-Host "Done. Installer under: $Release" -ForegroundColor Green
Get-ChildItem -Path $Release -Filter *.exe -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $($_.FullName)" }
