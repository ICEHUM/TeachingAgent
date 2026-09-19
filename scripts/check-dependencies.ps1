$ErrorActionPreference = 'Stop'
$taskProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:UV_CACHE_DIR = Join-Path $taskProjectRoot '.cache\uv'
$env:OPENHANDS_SUPPRESS_BANNER = '1'
$taskPython = Join-Path $taskProjectRoot 'backend\.venv\Scripts\python.exe'
Push-Location $taskProjectRoot
try {
    uv pip check --python $taskPython
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency consistency check failed' }
    & $taskPython (Join-Path $taskProjectRoot 'scripts\verify_backend.py')
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency import check failed' }
    npm.cmd run check:frontend
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build check failed' }
    npm.cmd ls --depth=0
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency consistency check failed' }
} finally {
    Pop-Location
}
