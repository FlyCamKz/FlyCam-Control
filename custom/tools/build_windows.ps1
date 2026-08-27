param(
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release",
    [string]$QtRoot = $env:QT_ROOT_DIR,
    [string]$BuildDirectory = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not $BuildDirectory) {
    $BuildDirectory = Join-Path (Split-Path $repositoryRoot -Parent) "build\FlyCam-Windows"
}

foreach ($command in @("cmake", "ninja")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' was not found in PATH."
    }
}

if (-not $QtRoot) {
    throw "Set QT_ROOT_DIR or pass -QtRoot with the Qt 6.11.1 MSVC 2022 x64 directory."
}

$qtToolchain = Join-Path $QtRoot "lib\cmake\Qt6\qt.toolchain.cmake"
if (-not (Test-Path $qtToolchain)) {
    throw "Qt toolchain file was not found at $qtToolchain"
}

Write-Host "Configuring FlyCam Drone Control Center"
cmake `
    -S $repositoryRoot `
    -B $BuildDirectory `
    -G "Ninja Multi-Config" `
    -DCMAKE_TOOLCHAIN_FILE="$qtToolchain" `
    -DQGC_BUILD_TESTING=OFF `
    -DQGC_ENABLE_GST_VIDEOSTREAMING=OFF

Write-Host "Building $Configuration"
cmake --build $BuildDirectory --config $Configuration --parallel

Write-Host "Creating Windows installer"
cmake --install $BuildDirectory --config $Configuration

$binary = Join-Path $BuildDirectory "$Configuration\FlyCam-Drone-Control-Center.exe"
if (-not (Test-Path $binary)) {
    throw "Build completed but $binary was not found."
}

$installer = Get-ChildItem -Path $BuildDirectory -Filter "*installer*.exe" | Select-Object -First 1
Write-Host "Executable: $binary"
if ($installer) {
    Write-Host "Installer:  $($installer.FullName)"
} else {
    Write-Warning "Executable was built, but an installer was not generated. Verify that NSIS is installed."
}
