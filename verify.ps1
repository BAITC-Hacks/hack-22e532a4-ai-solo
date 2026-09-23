$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
}

& $VenvPython (Join-Path $ProjectRoot 'scripts\verify.py')
exit $LASTEXITCODE
