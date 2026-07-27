# run_dashboard.ps1 — lance le dashboard local (http://127.0.0.1:8000)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location -Path $PSScriptRoot
python -m webapp.server
