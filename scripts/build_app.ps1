# Build VANTA.exe (native launcher) and create Start Menu / Desktop shortcuts.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'Run scripts\install.ps1 first (the Python environment is missing).' }

Push-Location $root
try {
    & $py scripts\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
    & $py -m PyInstaller --noconfirm --clean --onefile --windowed --name VANTA `
        --icon (Join-Path $root 'assets\vanta.ico') --distpath build\dist --workpath build\work --specpath build `
        scripts\launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    Copy-Item build\dist\VANTA.exe (Join-Path $root 'VANTA.exe') -Force

    $shell = New-Object -ComObject WScript.Shell
    $targets = @(
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'VANTA.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'VANTA.lnk')
    )
    foreach ($lnk in $targets) {
        $shortcut = $shell.CreateShortcut($lnk)
        $shortcut.TargetPath = (Join-Path $root 'VANTA.exe')
        $shortcut.WorkingDirectory = $root
        $shortcut.IconLocation = (Join-Path $root 'assets\vanta.ico')
        $shortcut.Description = 'VANTA - AI co-editor for DaVinci Resolve'
        $shortcut.Save()
    }
    Write-Host "Built $(Join-Path $root 'VANTA.exe') and created Start Menu + Desktop shortcuts."
}
finally {
    Pop-Location
}
