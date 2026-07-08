# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Shell Worker Platform v2.0.0 (Windows x64)

Produces two self-contained single-file EXEs (no shared _internal/):

  dist/shell-worker.exe      — console CLI (core only, ~10 MB)
  dist/shell-worker-gui.exe  — windowed GUI (core + PySide6, ~46 MB)

Both are moved to the project root by build_portable_exe.bat.
modules/ workflows/ resources/ are external, never bundled.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821

# ---------------------------------------------------------------------------
# Common excludes — never used by this project
# ---------------------------------------------------------------------------
_COMMON_EXCLUDES = [
    'tkinter',
    'unittest',
    'test',
]

# Qt sub-modules that pull in heavy native DLLs (WebEngine, Quick/QML,
# 3D, Multimedia, Charts, etc.).  The GUI only requires QtCore, QtGui,
# QtWidgets, and QtNetwork so these are safe to prune.
_QT_HEAVY_EXCLUDES = [
    # WebEngine — Chromium runtime, ~200 MB
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel',
    # Quick / QML stack, ~100 MB
    'PySide6.QtQuick',
    'PySide6.QtQuickWidgets',
    'PySide6.QtQuick3D',
    'PySide6.QtQuick3DHelpers',
    'PySide6.QtQuickControls2',
    'PySide6.QtQuickTemplates2',
    'PySide6.QtQuickTimeline',
    'PySide6.QtQml',
    'PySide6.QtQmlCore',
    'PySide6.QtQmlLocalStorage',
    'PySide6.QtQmlModels',
    'PySide6.QtQmlWorkerScript',
    'PySide6.QtQmlXmlListModel',
    'PySide6.QtShaderTools',
    # 3D, ~30 MB
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    # Multimedia, ~50 MB
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtSpatialAudio',
    # Charts / visualization, ~20 MB
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtGraphs',
    'PySide6.QtGraphsWidgets',
    # Other never-used modules
    'PySide6.QtBodymovin',
    'PySide6.QtDesigner',
    'PySide6.QtHelp',
    'PySide6.QtLocation',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtConcurrent',
    'PySide6.QtHttpServer',
    'PySide6.QtProtobuf',
    'PySide6.QtGrpc',
    'PySide6.QtBluetooth',
    'PySide6.QtNfc',
    'PySide6.QtSensors',
    'PySide6.QtSerialPort',
    'PySide6.QtSql',
    'PySide6.QtTest',
    'PySide6.QtUiTools',
    'PySide6.QtAxContainer',
    'PySide6.QtDBus',
    'PySide6.QtRemoteObjects',
    'PySide6.QtScxml',
    'PySide6.QtStateMachine',
    'PySide6.QtTextToSpeech',
    'PySide6.QtWebSockets',
    'PySide6.QtWebView',
    'PySide6.QtNetworkInformation',
    'PySide6.QtLabsAnimation',
    'PySide6.QtLabsFolderListModel',
    'PySide6.QtLabsSettings',
    'PySide6.QtLabsSharedImage',
    'PySide6.QtLabsWavefrontMesh',
]

# ============================================================================
# Analysis A — CLI (core + PyYAML + pywinpty, NO Qt)
# ============================================================================
a_cli = Analysis(
    ['main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_COMMON_EXCLUDES + [
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
    ],
    noarchive=False,
    optimize=0,
)

pyz_cli = PYZ(a_cli.pure)  # noqa: F821

cli_exe = EXE(  # noqa: F821
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.datas,
    exclude_binaries=False,
    name='shell-worker',
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

# ============================================================================
# Analysis B — GUI (core + PySide6, heavy Qt modules excluded)
# ============================================================================
a_gui = Analysis(
    ['main_gui.pyw'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PySide6.QtWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtNetwork',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_COMMON_EXCLUDES + _QT_HEAVY_EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz_gui = PYZ(a_gui.pure)  # noqa: F821

gui_exe = EXE(  # noqa: F821
    pyz_gui,
    a_gui.scripts,
    a_gui.binaries,
    a_gui.datas,
    exclude_binaries=False,
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
