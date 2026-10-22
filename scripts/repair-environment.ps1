$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$uvPath = Join-Path $repoRoot '.local\bin\uv.exe'
$pythonRoot = Join-Path $repoRoot '.local\python'
if (-not (Test-Path $uvPath)) { throw 'uv not found. Install uv from https://docs.astral.sh/uv/ or restore .local\bin\uv.exe.' }
$pythonPath = Get-ChildItem $pythonRoot -Directory -Filter 'cpython-3.12.*-windows-x86_64-none' | Sort-Object Name -Descending | Select-Object -First 1
if (-not $pythonPath) { throw 'Python 3.12 is missing. Restore the .local\python directory first.' }
$pythonExe = Join-Path $pythonPath.FullName 'python.exe'
$env:UV_CACHE_DIR = Join-Path $repoRoot '.local\uv-cache'
& $uvPath venv --relocatable --clear --python $pythonExe (Join-Path $repoRoot 'src\.venv')
if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment.' }
& $uvPath sync --project (Join-Path $repoRoot 'src') --python $pythonExe --locked --extra dev --extra legacy-excel --link-mode copy
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network/proxy settings.' }
Write-Host 'Environment ready. Start XiaochiLauncher.exe.'
