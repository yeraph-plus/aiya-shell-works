@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================================
::  Shell Worker Platform — Windows x64 Build Script
::
::  Produces directly at project root (same level as main_cli.py):
::    shell-worker-cli.exe   (console)
::    shell-worker-gui.exe   (windowed)
::    _internal\             (shared runtime, no duplication)
::
::  modules\ workflows\ resources\ are already at project root —
::  they are external, not compiled, and share the parent dir with the exe.
::
::  Prerequisites:
::    - Python 3.11+ on PATH
::    - PyInstaller  (auto-installed if missing)
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
    echo [1/4] Installing PyInstaller ...
    pip install pyinstaller -q
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install PyInstaller.
        pause
        exit /b 1
    )
) else (
    echo [1/4] PyInstaller OK.
)

:: ---- Run PyInstaller with .spec ----
echo.
echo [2/4] Building executables ...
echo        This may take a few minutes on first run.
echo.

call pyinstaller shell-worker-portable.spec --distpath "%PROJECT_ROOT%" --workpath "%PROJECT_ROOT%build" --clean --noconfirm
if %errorlevel% neq 0 (
    echo.
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

:: ---- Flatten output to project root ----
echo.
echo [3/4] Flattening to project root ...

set "STAGE_DIR=%PROJECT_ROOT%shell-worker"

if exist "%STAGE_DIR%\shell-worker-cli.exe" (
    move /y "%STAGE_DIR%\shell-worker-cli.exe" "%PROJECT_ROOT%\" >nul
    echo        shell-worker-cli.exe
)
if exist "%STAGE_DIR%\shell-worker-gui.exe" (
    move /y "%STAGE_DIR%\shell-worker-gui.exe" "%PROJECT_ROOT%\" >nul
    echo        shell-worker-gui.exe
)
if exist "%STAGE_DIR%\_internal" (
    if exist "%PROJECT_ROOT%\_internal" (
        rmdir /s /q "%PROJECT_ROOT%\_internal" >nul 2>&1
    )
    move /y "%STAGE_DIR%\_internal" "%PROJECT_ROOT%\"
    echo        _internal\
)

rmdir /s /q "%STAGE_DIR%" >nul 2>&1

:: ---- Done ----
echo.
echo [4/4] Build complete.
echo.
echo ========================================
echo   Output: %PROJECT_ROOT%
echo ========================================
echo   shell-worker-cli.exe    (console)
echo   shell-worker-gui.exe    (windowed / PySide6)
echo   _internal\              (shared runtime)
echo ========================================
echo.

:: Clean up build temp
rmdir /s /q "%PROJECT_ROOT%build" >nul 2>&1

pause
