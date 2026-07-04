# register_task.ps1 — enregistre la collecte dans le Planificateur de tâches
# Windows, toutes les 20 minutes, en continu.
#
#   Lancer UNE fois, dans PowerShell, depuis le dossier du projet :
#       .\register_task.ps1
#
#   Pour supprimer la tâche plus tard :
#       Unregister-ScheduledTask -TaskName "GetaroundGBFS" -Confirm:$false
#
# La tâche tourne quand la session est ouverte. Le PC doit être allumé à l'heure
# prévue (sinon, passer à une VM/serveur — voir README).

$TaskName = "GetaroundGBFS"
$Script   = Join-Path $PSScriptRoot "run.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""

# déclencheur : maintenant, puis répétition toutes les 20 min sans fin
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 20)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Collecte GBFS Getaround IDF (étude marché/ML)" `
    -Force

Write-Host "Tâche '$TaskName' enregistrée : collecte toutes les 20 min."
Write-Host "Vérifier :  Get-ScheduledTask -TaskName $TaskName"
