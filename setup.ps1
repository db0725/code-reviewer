$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$reviewPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $reviewPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
}
& $reviewPython -m pip install --only-binary=:all: --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install project dependencies.' }
