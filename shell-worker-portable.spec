# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Shell Worker Platform v2.0.0 (Windows x64)

Produces a single ``dist/shell-worker/`` directory with:
  * shell-worker-cli.exe  — console entry (core only at runtime)
  * shell-worker-gui.exe  — windowed entry (core + PySide6)
  * _internal/            — shared runtime (one copy, NO duplication)
  * modules/              — external .py modules (not compiled)
  * workflows/            — external .yaml workflows
  * resources/            — external tools / installers

All three data directories are *excluded* from the PyInstaller bundle so
users can add/remove modules and workflows without rebuilding.  They are
copied into the dist by ``windows_build_exe.bat`` after the build finishes.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821

# ---------------------------------------------------------------------------
# Collect PySide6 hook data so the GUI entry can bootstrap the Qt runtime.
# ---------------------------------------------------------------------------
try:
    from PyInstaller.utils.hooks import collect_data_files
    _pyside_datas = collect_data_files('PySide6')
except Exception:
    _pyside_datas = []

# ---------------------------------------------------------------------------
# Single Analysis with BOTH entry points.
# ``exclude_binaries=True`` on EXE() keeps each .exe thin (~3 MB).
# COLLECT() puts all shared binaries + data into ``_internal/`` once.
# ---------------------------------------------------------------------------
a = Analysis(
    ['main_cli.py', 'main_gui.pyw'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_pyside_datas,
    hiddenimports=[
        'PySide6.QtWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtNetwork',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'test',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

# -- CLI entry (console visible) --------------------------------------------
cli_exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='shell-worker-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# -- GUI entry (windowed — no console flash) ---------------------------------
gui_exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='shell-worker-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# -- One COLLECT → single _internal/ shared by both EXEs ---------------------
coll = COLLECT(  # noqa: F821
    cli_exe,
    gui_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='shell-worker',
)
