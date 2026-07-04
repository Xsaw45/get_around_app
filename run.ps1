# run.ps1 — un passage de collecte, appelé par le Planificateur de tâches.
# Force l'UTF-8 (sinon la console Windows plante sur les accents) et journalise.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location -Path $PSScriptRoot
$ts = Get-Date -Format "s"
try {
    $out = python ingest.py 2>&1 | Out-String
    Add-Content -Path "run.log" -Value "[$ts] OK`n$out" -Encoding utf8
} catch {
    Add-Content -Path "run.log" -Value "[$ts] ERREUR: $_" -Encoding utf8
    exit 1
}
