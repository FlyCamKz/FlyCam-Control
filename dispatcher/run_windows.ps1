param(
    [string]$ListenAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8088,
    [string]$ApiKey = $env:FLYCAM_API_KEY,
    [string]$DatabasePath = ""
)

$ErrorActionPreference = "Stop"

if (($ListenAddress -ne "127.0.0.1") -and ($ListenAddress -ne "localhost") -and (-not $ApiKey)) {
    throw "An API key is required when listening outside localhost."
}

if (-not $DatabasePath) {
    $dataDirectory = Join-Path $PSScriptRoot "data"
    New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
    $DatabasePath = Join-Path $dataDirectory "flycam-dispatcher.sqlite3"
}

$serverPath = Join-Path $PSScriptRoot "server.py"
$serverArguments = @(
    $serverPath,
    "--host", $ListenAddress,
    "--port", $Port,
    "--db", $DatabasePath
)
if ($ApiKey) {
    $serverArguments += @("--api-key", $ApiKey)
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @serverArguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @serverArguments
} else {
    throw "Python 3.11 or newer was not found."
}
