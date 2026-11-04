"""Desktop shortcuts for TrackSound (``tracksound shortcut``; the installers run it).

- Windows: TrackSound.lnk on the Desktop and in the Start menu.
- macOS: TrackSound.command on the Desktop (double-click opens Terminal running TrackSound).
- Linux: tracksound.desktop in the applications menu (and on the Desktop if there is one).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def _executable() -> str:
    """The `tracksound` launcher to point shortcuts at."""
    if getattr(sys, "frozen", False):                     # the standalone app
        return sys.executable
    found = shutil.which("tracksound")
    if found:
        return found
    exe = Path(sys.argv[0]).resolve()
    return str(exe)


def _desktop() -> Path | None:
    for d in (os.environ.get("XDG_DESKTOP_DIR"), Path.home() / "Desktop"):
        if d and Path(d).is_dir():
            return Path(d)
    return None


def _ps_quote(value) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _windows_shortcuts(exe: str) -> list[Path]:
    """Desktop and Start menu shortcuts. Windows is asked for the real folders (the Desktop is
    often redirected into OneDrive). Paths are embedded as quoted literals: with -Command,
    PowerShell does not pass extra arguments to the script."""
    icon = DATA / "icon.ico"
    ps = (
        "$made = @();"
        "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {"
        "  if (-not $dir) { continue };"
        "  $lnk = Join-Path $dir 'TrackSound.lnk';"
        "  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);"
        f"  $s.TargetPath = {_ps_quote(exe)};"
        f"  $s.IconLocation = {_ps_quote(icon)};"
        "  $s.WorkingDirectory = $env:USERPROFILE;"
        "  $s.Description = 'TrackSound: where and when was it recorded?';"
        "  $s.Save(); $made += $lnk };"
        "$made -join [Environment]::NewLine"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, check=True)
    return [Path(line.strip()) for line in r.stdout.splitlines() if line.strip()]


def create() -> list[Path]:
    exe = _executable()
    made: list[Path] = []
    if sys.platform == "win32":
        made += _windows_shortcuts(exe)
    elif sys.platform == "darwin":
        desk = _desktop() or Path.home()
        cmd = desk / "TrackSound.command"
        cmd.write_text(f'#!/bin/sh\nexec "{exe}"\n')
        cmd.chmod(0o755)
        made.append(cmd)
    else:
        apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        entry = ("[Desktop Entry]\nType=Application\nName=TrackSound\n"
                 "Comment=Where and when was it recorded? Acoustic geolocation\n"
                 f'Exec="{exe}"\nIcon={DATA / "icon.png"}\nTerminal=true\nCategories=AudioVideo;Science;\n')
        f = apps / "tracksound.desktop"
        f.write_text(entry)
        f.chmod(0o755)
        made.append(f)
        desk = _desktop()
        if desk:
            g = desk / "tracksound.desktop"
            g.write_text(entry)
            g.chmod(0o755)
            made.append(g)
    return made
