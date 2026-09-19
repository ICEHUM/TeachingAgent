$ErrorActionPreference = 'Stop'
$taskProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:UV_CACHE_DIR = Join-Path $taskProjectRoot '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $taskProjectRoot '.tools\python'
Push-Location $taskProjectRoot
try {
    uv python install 3.12.13 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw 'Project Python installation failed' }
    uv sync --project (Join-Path $taskProjectRoot 'backend') --python 3.12.13 --managed-python --locked
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed' }
    npm.cmd ci --cache (Join-Path $taskProjectRoot '.cache\npm') --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
} finally {
    Pop-Location
}
