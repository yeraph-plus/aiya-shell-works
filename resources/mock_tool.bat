@echo off
rem mock_tool.bat — simulates calling an external binary from resources/.
rem Usage: mock_tool.bat <file_path>
rem Emits stdout / stderr lines, creates a .done sidecar, exits 0.

setlocal
echo [mock_tool] args: %*
echo [mock_tool] stdout stream
echo [mock_tool] stderr stream 1>&2
if not "%~1"=="" (
    type nul > "%~1.done"
)
exit /b 0