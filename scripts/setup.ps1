# Kept for compatibility: the full installer lives in install.ps1.
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'install.ps1') @args
exit $LASTEXITCODE
