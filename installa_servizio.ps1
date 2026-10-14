<#
Registra l'attività pianificata "Jarvis sorveglia": all'accensione del PC e poi ogni 5 minuti lancia sorveglia.py, che
avvia Jarvis in modo servizio (Telegram, pagina web, messaggi automatici) se non è acceso. Gira anche senza nessuno
connesso a Windows ("Esegui indipendentemente dalla connessione dell'utente", senza salvare la password: S4U), quindi
Jarvis riparte da solo dopo i riavvii di Windows Update e dopo un guasto.

Va lanciato UNA volta da amministratore (tasto destro su Start -> Terminale (amministratore)):

    powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\marco\Desktop\prog\jarvis\installa_servizio.ps1"

Per toglierla: Unregister-ScheduledTask -TaskName "Jarvis sorveglia" -Confirm:$false (da amministratore).
Se uv aggiorna Python va rilanciato, come crea_icona.py.
#>
$ErrorActionPreference = 'Stop'
$cartella = $PSScriptRoot
$casa = ((Get-Content (Join-Path $cartella '.venv\pyvenv.cfg')) -match '^home\s*=')[0] -replace '^home\s*=\s*', ''
$pythonw = Join-Path $casa 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Non trovo $pythonw" }

$attuale = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$attuale).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Serve il Terminale da amministratore (tasto destro su Start -> Terminale (amministratore))."
}
# l'utente di Windows che usa Jarvis, non l'amministratore che lancia lo script
$utente = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $utente) { $utente = "$env:USERDOMAIN\$env:USERNAME" }

$azione = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$(Join-Path $cartella 'sorveglia.py')`"" `
    -WorkingDirectory $cartella
$accensione = New-ScheduledTaskTrigger -AtStartup
$ogni5 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$chi = New-ScheduledTaskPrincipal -UserId $utente -LogonType S4U -RunLevel Limited
$impostazioni = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)

Register-ScheduledTask -TaskName 'Jarvis sorveglia' -Action $azione -Trigger @($accensione, $ogni5) -Principal $chi `
    -Settings $impostazioni -Description 'Tiene acceso Jarvis (sorveglia.py): all''accensione e ogni 5 minuti' -Force |
    Out-Null
Write-Output "Attività 'Jarvis sorveglia' registrata per $utente (all'accensione e ogni 5 minuti)."
