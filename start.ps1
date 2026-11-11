param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$reviewPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $reviewPython)) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
& $reviewPython -X utf8 server.py --port $Port
