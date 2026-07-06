#!/usr/bin/env bash
# mock_tool.sh — linux/macOS fallback of mock_tool.bat.
# Usage: mock_tool.sh <file_path>
set -e
echo "[mock_tool] args: $@"
echo "[mock_tool] stdout stream"
echo "[mock_tool] stderr stream" >&2
if [ -n "$1" ]; then
    touch "$1.done"
fi
exit 0