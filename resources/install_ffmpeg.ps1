# Install FFmpeg static build (win64)
# Source: https://github.com/BtbN/FFmpeg-Builds/releases
#
# Usage: powershell -File resources/install_ffmpeg.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$releaseUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
$zipPath = Join-Path $env:TEMP "ffmpeg.zip"

Write-Host "Downloading FFmpeg from $releaseUrl ..."
Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath

Write-Host "Extracting..."
$extractDir = Join-Path $env:TEMP "ffmpeg_extract"
Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue

# Detect structure: single subdir -> use as-is; flat -> wrap
$items = Get-ChildItem -Path $extractDir
if ($items.Count -eq 1 -and $items[0].PSIsContainer) {
    $innerDir = $items[0]
    $destDir = Join-Path $PSScriptRoot $innerDir.Name
} else {
    $innerDir = $extractDir
    $destDir = Join-Path $PSScriptRoot "ffmpeg"
}

Remove-Item -Path $destDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Split-Path $destDir -Parent) -Force -ErrorAction SilentlyContinue | Out-Null
Move-Item -Path $innerDir.FullName -Destination $destDir -Force

Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue

$ffmpegPath = Get-ChildItem -Path $destDir -Filter "ffmpeg.exe" -Recurse -File | Select-Object -First 1
if ($ffmpegPath) {
    Write-Host "ffmpeg.exe found: $($ffmpegPath.FullName)"
} else {
    Write-Warning "ffmpeg.exe not found. Check $destDir"
}

$allFiles = (Get-ChildItem -Path $destDir -Recurse -File).Count
Write-Host "FFmpeg installed: $destDir ($allFiles files)"
Write-Host "Installation complete."
