# Install VapourSynth portable (Windows x64)
# Source: https://github.com/vapoursynth/vapoursynth/releases
#
# Usage: powershell -File resources/install_vapoursynth.ps1
#   Or specify version: powershell -File resources/install_vapoursynth.ps1 -Version R77

param(
    [string]$Version = "R77"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Try multiple URL patterns (naming convention varies between releases)
$urlPatterns = @(
    "https://github.com/vapoursynth/vapoursynth/releases/download/$Version/VapourSynth64-Portable_$Version.7z",
    "https://github.com/vapoursynth/vapoursynth/releases/download/$Version/VapourSynth64-Portable-$Version.7z",
    "https://github.com/vapoursynth/vapoursynth/releases/download/$Version/VapourSynth64-Portable_$Version.zip",
    "https://github.com/vapoursynth/vapoursynth/releases/download/$Version/VapourSynth64-Portable-$Version.zip"
)

$archivePath = $null
$downloaded = $false

foreach ($url in $urlPatterns) {
    $ext = if ($url -match "\.zip$") { ".zip" } else { ".7z" }
    $archivePath = Join-Path $env:TEMP "vapoursynth_archive$ext"
    Write-Host "Trying: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $archivePath -ErrorAction Stop
        $downloaded = $true
        Write-Host "Downloaded successfully."
        break
    } catch {
        Write-Host "  Failed: $_"
    }
}

if (-not $downloaded) {
    Write-Host ""
    Write-Host "Automatic download failed. Please download manually:"
    Write-Host "  1. Visit https://github.com/vapoursynth/vapoursynth/releases"
    Write-Host "  2. Download the portable .7z/.zip for Windows (x64)"
    Write-Host "  3. Extract and place under: $($PSScriptRoot)vapoursynth/"
    Write-Host ""
    Write-Host "Expected: resources/vapoursynth/VSPipe.exe"
    exit 0
}

# ---- Extract: preserve archive-native structure ----
$extractDir = Join-Path $env:TEMP "vapoursynth_extract"
Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $extractDir -Force | Out-Null

Write-Host "Extracting..."

if ($archivePath -match "\.7z$") {
    $7z = Get-Command "7z.exe" -ErrorAction SilentlyContinue
    if (-not $7z) {
        $7zCandidates = @(
            "$env:ProgramFiles\7-Zip\7z.exe",
            "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
        )
        foreach ($c in $7zCandidates) { if (Test-Path $c) { $7z = $c; break } }
    }
    if ($7z) {
        & $7z x $archivePath -o"$extractDir" -y | Out-Null
    } else {
        Write-Error "7-Zip is required for .7z archives. Please install from https://7-zip.org/ or download the .zip version."
        Remove-Item -Path $archivePath -Force -ErrorAction SilentlyContinue
        exit 1
    }
} else {
    Expand-Archive -Path $archivePath -DestinationPath $extractDir -Force
}

Remove-Item -Path $archivePath -Force -ErrorAction SilentlyContinue

# Detect structure: single subdir -> use as-is; flat files -> wrap
$items = Get-ChildItem -Path $extractDir
if ($items.Count -eq 1 -and $items[0].PSIsContainer) {
    $innerDir = $items[0]
    $destDir = Join-Path $PSScriptRoot $innerDir.Name
    Write-Host "Archive contains single subdirectory: $($innerDir.Name)"
} else {
    $innerDir = $extractDir
    $destDir = Join-Path $PSScriptRoot "vapoursynth"
    Write-Host "Archive is flat, wrapping in: vapoursynth/"
}

Remove-Item -Path $destDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Split-Path $destDir -Parent) -Force -ErrorAction SilentlyContinue | Out-Null
Move-Item -Path $innerDir.FullName -Destination $destDir -Force

Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue

$allFiles = (Get-ChildItem -Path $destDir -Recurse -File).Count
Write-Host "VapourSynth installed: $destDir ($allFiles files)"

# Quick test
$vspipePath = Get-ChildItem -Path $destDir -Filter "VSPipe.exe" -Recurse -File | Select-Object -First 1
if ($vspipePath) {
    Write-Host "VSPipe.exe found: $($vspipePath.FullName)"
    try {
        & $vspipePath.FullName --version 2>&1 | Select-Object -First 3
    } catch {
        Write-Host "  (version check skipped)"
    }
    Write-Host "Installation complete."
} else {
    Write-Warning "VSPipe.exe not found in extracted files."
    Write-Host "Expected: $destDir/**/VSPipe.exe"
}
