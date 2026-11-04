# TrackSound installer for Windows. Run in PowerShell:
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Savvyboi/TrackSound/HEAD/install.ps1 | iex"
#
# It installs uv (Astral's Python installer) if needed, then TrackSound with its own Python and
# ffmpeg, adds Start menu and Desktop shortcuts, and starts it. No admin rights needed.
# Uninstall: `uv tool uninstall tracksound`.
#
# Options (environment variables): TRACKSOUND_REF=<branch or tag> installs that version from
# GitHub instead of the latest release; TRACKSOUND_NO_START=1 skips starting it.
$ErrorActionPreference = "Stop"
$Repo = if ($env:TRACKSOUND_REPO) { $env:TRACKSOUND_REPO } else { "Savvyboi/TrackSound" }

Write-Host "==> Installing TrackSound"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> Installing uv (https://docs.astral.sh/uv/)"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

$Src = $null
if (-not $env:TRACKSOUND_REF) {
    try {
        $rel = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
        $Src = ($rel.assets | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1).browser_download_url
    } catch { }
}
if (-not $Src) {
    $Ref = if ($env:TRACKSOUND_REF) { $env:TRACKSOUND_REF } else { "HEAD" }
    $Src = "tracksound @ https://github.com/$Repo/archive/$Ref.zip#subdirectory=backend"
}

Write-Host "==> Installing from $Src"
uv tool install --force --python 3.12 "$Src"
uv tool update-shell | Out-Null

$Exe = "$env:USERPROFILE\.local\bin\tracksound.exe"
& $Exe shortcut

Write-Host ""
Write-Host "TrackSound is installed. Start it any time from the Start menu or Desktop, or run: tracksound"
if (-not $env:TRACKSOUND_NO_START) {
    Write-Host "Starting it now (it opens in your browser; close its window to stop)..."
    Start-Process $Exe
}
