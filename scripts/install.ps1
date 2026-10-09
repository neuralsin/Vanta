# VANTA installer: Python environment, CUDA PyTorch, FFmpeg/Ollama, models, launcher, shortcuts, Resolve HUD.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-SkipModels] [-SkipTools]
param(
    [switch]$SkipModels,
    [switch]$SkipTools
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Yellow }

try {
    Step 'Python 3.12'
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if (-not $launcher) { throw 'Install Python 3.12 (python.org) with the py launcher, then rerun.' }
    $version = & $launcher.Source -3.12 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or $version -ne '3.12') { throw 'Python 3.12 is required side by side (VANTA never replaces existing Pythons).' }
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        & $launcher.Source -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating the virtual environment failed.' }
    }
    $py = Join-Path $root '.venv\Scripts\python.exe'
    $env:PIP_CACHE_DIR = Join-Path $root 'data\cache\pip'

    Step 'Pinned dependencies (CUDA 12.8 PyTorch for the RTX GPU)'
    & $py -m pip install --upgrade pip
    & $py -m pip install --requirement requirements.lock --extra-index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'Installing pinned dependencies failed.' }
    & $py -m pip install --editable . --no-deps
    if ($LASTEXITCODE -ne 0) { throw 'Installing VANTA failed.' }

    if (-not $SkipTools) {
        Step 'FFmpeg and Ollama'
        if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe")) {
            winget install --id Gyan.FFmpeg -e --scope user --accept-source-agreements --accept-package-agreements --silent
        }
        if (-not (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
            winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements --silent
        }
        $ollamaModels = Join-Path $root 'data\models\ollama'
        New-Item -ItemType Directory -Force $ollamaModels | Out-Null
        [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $ollamaModels, 'User')
        [Environment]::SetEnvironmentVariable('OLLAMA_NUM_PARALLEL', '1', 'User')
    }

    if (-not $SkipModels) {
        Step 'Models (director VLM via Ollama + specialist models via Hugging Face)'
        $env:OLLAMA_MODELS = Join-Path $root 'data\models\ollama'
        $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
        if (Test-Path $ollama) {
            if (-not (Get-Process -Name ollama -ErrorAction SilentlyContinue)) { Start-Process -WindowStyle Hidden $ollama -ArgumentList 'serve'; Start-Sleep 4 }
            & $ollama pull qwen3-vl:8b
        }
        & $py -c "from vanta.models.installer import install_defaults; import json; print(json.dumps(install_defaults(), indent=1))"
    }

    Step 'Fusion templates, capability probe, launcher and shortcuts'
    & $py -m vanta.resolve.templates.build_fusion_library
    & $py scripts\phase0_probe.py
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build_app.ps1')
    & $py -c "from vanta.app.hud_server import install_hud_script; print(install_hud_script(8765))"

    Step 'Done'
    Write-Host 'Start VANTA from the Start Menu or Desktop shortcut (or VANTA.exe in this folder).'
    Write-Host 'In DaVinci Resolve Studio: Preferences > System > General > External scripting using: Local.'
    Write-Host 'Then open System > Run compatibility check inside VANTA.'
}
finally {
    Pop-Location
}
