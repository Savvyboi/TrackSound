# PyInstaller spec for the standalone TrackSound app.
# Build (from backend/):  uv run --group bundle pyinstaller packaging/tracksound.spec --noconfirm
# Output: dist/TrackSound/ (a folder with the TrackSound executable and its libraries).
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = []
datas += collect_data_files("tracksound")                 # web UI, countries, county tables, demo data
datas += collect_data_files("imageio_ffmpeg")             # the bundled ffmpeg binary
datas += collect_data_files("certifi")
for dist in ("tracksound", "imageio-ffmpeg", "dopplertrack", "fastapi", "starlette", "uvicorn"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

hiddenimports = (collect_submodules("tracksound") + collect_submodules("uvicorn") + collect_submodules("dopplertrack")
                 + ["scipy.signal", "scipy.optimize", "scipy.spatial", "scipy.ndimage", "h5py", "soundfile"])

a = Analysis(
    ["launcher.py"],
    pathex=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib.backends.backend_tkagg", "matplotlib.backends.backend_qtagg", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TrackSound",
    console=True,                 # the window shows where TrackSound runs; closing it stops the server
    icon=os.path.join(SPECPATH, "icon.ico") if sys.platform == "win32" else None,   # noqa: F821 (SPECPATH: set by PyInstaller)
)
coll = COLLECT(exe, a.binaries, a.datas, name="TrackSound")
