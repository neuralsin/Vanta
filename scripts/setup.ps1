$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pyLauncher = Get-Command py -ErrorAction Stop
$version = & $pyLauncher.Source -3.12 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $version -ne '3.12') {
    throw 'Python 3.12+ is required. Install it side by side; VANTA does not replace existing Python versions.'
}

Push-Location $repoRoot
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        & $pyLauncher.Source -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating the Python virtual environment failed.' }
    }
    if (-not (Test-Path 'requirements.lock')) {
        throw 'requirements.lock is missing; refusing an unpinned environment install.'
    }
    & '.venv\Scripts\python.exe' -m pip install --requirement requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Installing pinned dependencies failed.' }
    & '.venv\Scripts\python.exe' -m pip install --editable . --no-deps
    if ($LASTEXITCODE -ne 0) { throw 'Installing VANTA in editable mode failed.' }
    & '.venv\Scripts\python.exe' scripts\phase0_probe.py
    if ($LASTEXITCODE -ne 0) { throw 'The Phase 0 capability probe failed.' }
    Write-Host 'VANTA environment is ready. Start Mission Control with:'
    Write-Host '.\.venv\Scripts\python.exe -m uvicorn vanta.live.app:app --host 127.0.0.1 --port 8765'
}
finally {
    Pop-Location
}
