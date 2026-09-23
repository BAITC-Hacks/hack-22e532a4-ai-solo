$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
}

& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
& $VenvPython -m uvicorn app.main:app --app-dir $ProjectRoot --host 127.0.0.1 --port 8000
