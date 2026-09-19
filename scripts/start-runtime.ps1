$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $taskRoot 'backend\.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'verify_runtime.py') --start --smoke
exit $LASTEXITCODE
