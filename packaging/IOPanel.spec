# Reproducible Windows x64 one-folder build. Dependencies are installed from uv.lock.
from pathlib import Path

from PyInstaller.building.datastruct import TOC

repo_root = Path(SPECPATH).parent
hiddenimports = ["resources.resources_rc"]

a = Analysis(
    [str(repo_root / "app.py")],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matlab", "matlabengine", "vmbpy"],
    noarchive=False,
)
# IOPanel obtains frames from the camera backends and uses OpenCV for array
# transforms only. No production path opens or writes video files, so retain
# OpenCV itself while omitting its optional FFmpeg video I/O plugin.
a.binaries = TOC(
    entry
    for entry in a.binaries
    if not Path(entry[0]).name.lower().startswith("opencv_videoio_ffmpeg")
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IOPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="IOPanel",
)
