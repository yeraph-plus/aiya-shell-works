# Install vs-mlrt plugin + ONNX models (Windows x64, CUDA)
# Source: https://github.com/AmusementClub/vs-mlrt/releases
#
# Usage: powershell -File resources/install_vsmlrt.ps1
#   Or specify version: powershell -File resources/install_vsmlrt.ps1 -Version v15.16

param(
    [string]$Version = "v15.16"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$modelDir = Join-Path $PSScriptRoot "models"
New-Item -ItemType Directory -Path $modelDir -Force | Out-Null

# ---- Download vs-mlrt plugin (with CUDA support) ----
$pluginUrl = "https://github.com/AmusementClub/vs-mlrt/releases/download/$Version/vsmlrt-cuda.$Version.7z"
$pluginArchive = Join-Path $env:TEMP "vsmlrt_cuda.7z"

Write-Host "============================================"
Write-Host "  vs-mlrt + VapourSynth ML Models Installer"
Write-Host "============================================"
Write-Host ""
Write-Host "[1/2] Downloading vs-mlrt CUDA plugin..."
Write-Host "  URL: $pluginUrl"
try {
    Invoke-WebRequest -Uri $pluginUrl -OutFile $pluginArchive -ErrorAction Stop
} catch {
    Write-Error "Download failed: $_"
    Write-Host "Please check the version number or download manually from:"
    Write-Host "  https://github.com/AmusementClub/vs-mlrt/releases"
    exit 1
}

Write-Host "  Extracting..."
$extractDir = Join-Path $env:TEMP "vsmlrt_extract"
Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $extractDir -Force | Out-Null

$7z = Get-Command "7z.exe" -ErrorAction SilentlyContinue
if (-not $7z) {
    $7zCandidates = @(
        "$env:ProgramFiles\7-Zip\7z.exe",
        "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
    )
    foreach ($c in $7zCandidates) { if (Test-Path $c) { $7z = $c; break } }
}

if ($7z) {
    & $7z x $pluginArchive -o"$extractDir" -y | Out-Null
} else {
    Write-Error "7-Zip is required to extract .7z archives. Please install from https://7-zip.org/."
    Remove-Item -Path $pluginArchive -Force -ErrorAction SilentlyContinue
    exit 1
}

Remove-Item -Path $pluginArchive -Force -ErrorAction SilentlyContinue

# Detect structure: single subdir -> use as-is; flat files -> wrap
$items = Get-ChildItem -Path $extractDir
if ($items.Count -eq 1 -and $items[0].PSIsContainer) {
    $innerDir = $items[0]
    $destDir = Join-Path $PSScriptRoot $innerDir.Name
} else {
    $innerDir = $extractDir
    $destDir = Join-Path $PSScriptRoot "vsmlrt"
}

Remove-Item -Path $destDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Split-Path $destDir -Parent) -Force -ErrorAction SilentlyContinue | Out-Null
Move-Item -Path $innerDir.FullName -Destination $destDir -Force

Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue

# Find vsmlrt.dll
$dll = Get-ChildItem -Path $destDir -Filter "vsmlrt.dll" -Recurse -File | Select-Object -First 1
if ($dll) {
    Write-Host "  vsmlrt.dll: $($dll.FullName)"
} else {
    Write-Warning "  vsmlrt.dll not found. Check $destDir"
}
Write-Host "  Installed to: $destDir"

# ---- Interactive model selection ----
Write-Host ""
Write-Host "[2/2] Select ML models to download:"
Write-Host ""

$models = @(
    @{ Id = 1;  Name = "rife-v4.6_ensemble";          Size = "~50MB";  Category = "RIFE 补帧";       Desc = "高精度 RIFE 4.6 集成模型 (推荐)" },
    @{ Id = 2;  Name = "rife-v4.15_lite";              Size = "~20MB";  Category = "RIFE 补帧";       Desc = "轻量 RIFE 4.15 快速模型" },
    @{ Id = 3;  Name = "RealESRGAN_x4plus";            Size = "~70MB";  Category = "ESRGAN 超分";      Desc = "Real-ESRGAN 4x 超分模型 (通用, 推荐)" },
    @{ Id = 4;  Name = "RealESRGAN_x2plus";            Size = "~70MB";  Category = "ESRGAN 超分";      Desc = "Real-ESRGAN 2x 超分模型" },
    @{ Id = 5;  Name = "SwinIR-L_x4";                  Size = "~150MB"; Category = "SwinIR 超分";       Desc = "SwinIR 超分模型 (高质量, 慢)" },
    @{ Id = 6;  Name = "dpir-denoise";                 Size = "~30MB";  Category = "DPIR 降噪";         Desc = "DPIR 神经网络降噪模型" },
    @{ Id = 7;  Name = "RealESRGANv2-animevideo-xsx4"; Size = "~70MB";  Category = "ESRGAN 超分";      Desc = "Real-ESRGAN 动漫视频 4x 超分" }
)

$categories = $models | Group-Object Category
foreach ($cat in $categories) {
    Write-Host "  [$($cat.Name)]"
    foreach ($m in $cat.Group) {
        Write-Host ("    [{0}] {1,-35} {2,8}  {3}" -f $m.Id, $m.Name, $m.Size, $m.Desc)
    }
    Write-Host ""
}

Write-Host "  ------"
Write-Host "  [A] 全部下载 (ALL models, ~460MB total)"
Write-Host "  [0] 跳过模型下载"
Write-Host ""

$selection = Read-Host "  输入编号 (空格分隔，如 1 3 4)，或 A/0"

$selectedIds = @()
if ($selection -eq "A" -or $selection -eq "a") {
    $selectedIds = $models | ForEach-Object { $_.Id }
    Write-Host "  选择了全部 $($models.Count) 个模型"
} elseif ($selection -eq "0") {
    Write-Host "  跳过模型下载。"
    Write-Host "  后续可手动下载 .onnx 模型文件放入: $modelDir"
    Write-Host ""
    Write-Host "  Install complete."
    exit 0
} else {
    $parts = $selection -split '\s+'
    foreach ($p in $parts) {
        $id = [int]$p
        if ($id -gt 0 -and $id -le $models.Count) {
            $selectedIds += $id
        }
    }
    if ($selectedIds.Count -eq 0) {
        Write-Host "  未选择有效编号，跳过模型下载。"
        exit 0
    }
}

$modelUrls = @{
    "rife-v4.6_ensemble"           = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/rife-v4.6_ensemble.onnx"
    "rife-v4.15_lite"              = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/rife-v4.15_lite.onnx"
    "RealESRGAN_x4plus"            = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/RealESRGAN_x4plus.onnx"
    "RealESRGAN_x2plus"            = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/RealESRGAN_x2plus.onnx"
    "SwinIR-L_x4"                  = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/SwinIR-L_x4.onnx"
    "dpir-denoise"                 = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/dpir-denoise.onnx"
    "RealESRGANv2-animevideo-xsx4" = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20240218/RealESRGANv2-animevideo-xsx4.onnx"
}

Write-Host ""
Write-Host "  Downloading selected models to $modelDir ..."

$downloadedCount = 0
$failedCount = 0
$manifest = @{}

foreach ($id in $selectedIds) {
    $model = $models | Where-Object { $_.Id -eq $id } | Select-Object -First 1
    $modelFileName = "$($model.Name).onnx"
    $destPath = Join-Path $modelDir $modelFileName

    if (Test-Path $destPath) {
        Write-Host "    [$id] $($model.Name) - already exists, skipping."
        $manifest[$model.Name] = $destPath
        $downloadedCount++
        continue
    }

    $url = $modelUrls[$model.Name]
    if (-not $url) {
        Write-Warning "    [$id] $($model.Name) - no download URL. Download manually."
        $failedCount++
        continue
    }

    Write-Host "    [$id] $($model.Name) ($($model.Size)) ..." -NoNewline
    try {
        Invoke-WebRequest -Uri $url -OutFile $destPath -ErrorAction Stop
        Write-Host " OK"
        $manifest[$model.Name] = $destPath
        $downloadedCount++
    } catch {
        Write-Host " FAILED ($_)"
        Write-Warning "       Manual: $url -> $destPath"
        $failedCount++
    }
}

$manifestPath = Join-Path $modelDir "manifest.json"
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "============================================"
Write-Host "  Install Summary"
Write-Host "============================================"
Write-Host "  Plugin DLL : $destDir"
Write-Host "  Models     : $downloadedCount downloaded, $failedCount failed"
Write-Host "  Model dir  : $modelDir"
Write-Host "  Manifest   : $manifestPath"
Write-Host ""
Write-Host "  You can re-run this script anytime to add more models."
Write-Host "============================================"
