param(
    [string]$ListenAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8088,
    [string]$ApiKey = $env:FLYCAM_API_KEY,
    [string]$AdminKey = $env:FLYCAM_ADMIN_KEY,
    [string]$IngestKey = $env:FLYCAM_INGEST_KEY,
    [string]$ViewerKey = $env:FLYCAM_VIEWER_KEY,
    [string]$OperatorKey = $env:FLYCAM_OPERATOR_KEY,
    [string]$TlsCertificate = $env:FLYCAM_TLS_CERT,
    [string]$TlsPrivateKey = $env:FLYCAM_TLS_KEY,
    [string]$TlsCa = $env:FLYCAM_TLS_CA,
    [switch]$RequireClientCertificate,
    [string]$DataKeys = $env:FLYCAM_DATA_KEYS,
    [string]$ActiveDataKey = $env:FLYCAM_ACTIVE_DATA_KEY,
    [switch]$RequireDataEncryption,
    [string]$DatabasePath = ""
)

$ErrorActionPreference = "Stop"

if (($ListenAddress -ne "127.0.0.1") -and ($ListenAddress -ne "localhost") -and
    (-not $ApiKey) -and (-not $AdminKey) -and (-not $IngestKey) -and (-not $ViewerKey) -and (-not $OperatorKey) -and
    (-not $RequireClientCertificate)) {
    throw "An API key or required client certificate is needed outside localhost."
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
if ($AdminKey) {
    $serverArguments += @("--admin-key", $AdminKey)
}
if ($IngestKey) {
    $serverArguments += @("--ingest-key", $IngestKey)
}
if ($ViewerKey) {
    $serverArguments += @("--viewer-key", $ViewerKey)
}
if ($OperatorKey) {
    $serverArguments += @("--operator-key", $OperatorKey)
}
if ($TlsCertificate) {
    $serverArguments += @("--tls-cert", $TlsCertificate)
}
if ($TlsPrivateKey) {
    $serverArguments += @("--tls-key", $TlsPrivateKey)
}
if ($TlsCa) {
    $serverArguments += @("--tls-ca", $TlsCa)
}
if ($RequireClientCertificate) {
    $serverArguments += "--require-client-cert"
}
if ($DataKeys) {
    $env:FLYCAM_DATA_KEYS = $DataKeys
}
if ($ActiveDataKey) {
    $env:FLYCAM_ACTIVE_DATA_KEY = $ActiveDataKey
}
if ($RequireDataEncryption) {
    $serverArguments += "--require-data-encryption"
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @serverArguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @serverArguments
} else {
    throw "Python 3.11 or newer was not found."
}
