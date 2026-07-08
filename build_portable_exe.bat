@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================================
::  Shell Worker Platform — Windows x64 Build Script
::
::  Produces two self-contained single-file EXEs at project root:
::    shell-worker.exe        (console / CLI, ~10 MB)
::    shell-worker-gui.exe    (windowed / GUI + PySide6, ~46 MB)
::
::  One PyInstaller run builds both via shell-worker-portable.spec.
::  No _internal/ directory, no COLLECT — each EXE is fully self-contained.
::
::  modules\ workflows\ resources\ are already at project root —
::  they are external and shared by both EXEs.
::
::  Prerequisites:
::    - Python 3.11+ on PATH
::    - PyInstaller (auto-installed if missing)
:: ============================================================================

title Shell Worker — Build

echo.
echo ========================================
echo   Shell Worker Platform — Build
echo ========================================
echo.

:: ---- Locate project root ----
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

:: ---- Ensure PyInstaller is available ----
python -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/3] Installing PyInstaller ...
    pip install pyinstaller -q
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install PyInstaller.
        pause
        exit /b 1
    )
) else (
    echo [1/3] PyInstaller OK.
)

:: ---- Run PyInstaller (one pass builds both EXEs) ----
echo.
echo [2/3] Building executables ...
echo        This may take a few minutes on first run.
echo.

call pyinstaller shell-worker-portable.spec --distpath "%PROJECT_ROOT%dist" --workpath "%PROJECT_ROOT%build" --noconfirm
if %errorlevel% neq 0 (
    echo.
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

:: ---- Deploy EXEs to project root ----
echo.
echo [3/3] Deploying to project root ...

set "DIST=%PROJECT_ROOT%dist"

if exist "%DIST%\shell-worker.exe" (
    move /y "%DIST%\shell-worker.exe" "%PROJECT_ROOT%\" >nul
    echo        shell-worker.exe
)
if exist "%DIST%\shell-worker-gui.exe" (
    move /y "%DIST%\shell-worker-gui.exe" "%PROJECT_ROOT%\" >nul
    echo        shell-worker-gui.exe
)

:: ---- Cleanup ----
if exist "%DIST%" rmdir /s /q "%DIST%" >nul 2>&1
if exist "%PROJECT_ROOT%build" rmdir /s /q "%PROJECT_ROOT%build" >nul 2>&1

:: ---- Done ----
echo.
echo ========================================
echo   Build complete
echo   Output: %PROJECT_ROOT%
echo ========================================
echo   shell-worker.exe        (console / CLI)
echo   shell-worker-gui.exe    (windowed / PySide6)
echo   modules\ workflows\     (external)
echo ========================================
echo.

pause
