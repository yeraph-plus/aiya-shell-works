# Install ExifTool - extracts Windows executable package directly into resources/
# Source: https://exiftool.org/

$version = "13.54"
$toolDir = Join-Path $PSScriptRoot "exiftool-$version"
$zipUrl = "https://exiftool.org/exiftool-$version.zip"
$zipPath = Join-Path $env:TEMP "exiftool.zip"
$extractDir = Join-Path $env:TEMP "exiftool_extract"

Write-Host "Downloading ExifTool $version from $zipUrl ..."
try {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -ErrorAction Stop
} catch {
    Write-Error "Download failed: $_"
    exit 1
}

Write-Host "Extracting to $toolDir ..."
Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
$fileCount = 0
Get-ChildItem -Path $extractDir -Recurse -File | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $toolDir $_.Name) -Force
    $fileCount++
}

Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue
Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "ExifTool $version installed: $toolDir ($fileCount files)"