# Install aria2 portable executable (win64)
# Source: https://github.com/aria2/aria2/releases

$toolDir = Join-Path $PSScriptRoot "aria2"
$releaseUrl = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
$zipPath = Join-Path $env:TEMP "aria2.zip"

Write-Host "Downloading aria2 from $releaseUrl ..."
Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath

Write-Host "Extracting aria2c.exe to $toolDir ..."
New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
Expand-Archive -Path $zipPath -DestinationPath $env:TEMP\aria2_extract -Force

$exePath = Get-ChildItem -Path $env:TEMP\aria2_extract -Filter "aria2c.exe" -Recurse | Select-Object -First 1
if ($exePath) {
    Copy-Item -Path $exePath.FullName -Destination $toolDir -Force
    Write-Host "Installed: $(Join-Path $toolDir 'aria2c.exe')"
} else {
    Write-Error "aria2c.exe not found in extracted archive."
}

Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue
Remove-Item -Path $env:TEMP\aria2_extract -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "aria2 installation complete."
