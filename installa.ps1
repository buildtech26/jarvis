# installa.ps1 - Configura Jarvis su questo PC:
# dipendenze, avvio automatico, icona sul desktop, alimentazione sempre attiva.
#
# Uso (da PowerShell, dentro la cartella jarvis):
#   powershell -ExecutionPolicy Bypass -File .\installa.ps1
#
# Parametri opzionali:
#   -FileAvvio "main.py"          file Python da eseguire
#   -ArgomentiAvvio ""            argomenti extra per l'avvio automatico
#   -NomeTask "Jarvis"            nome dell'attivita pianificata

param(
    [string]$FileAvvio = "main.py",
    [string]$ArgomentiAvvio = "",
    [string]$NomeTask = "Jarvis"
)

$ErrorActionPreference = "Stop"
$cartella = $PSScriptRoot
Set-Location $cartella

function Passo($testo) { Write-Host "`n==> $testo" -ForegroundColor Cyan }
function Ok($testo)    { Write-Host "    OK: $testo" -ForegroundColor Green }
function Avviso($testo){ Write-Host "    ATTENZIONE: $testo" -ForegroundColor Yellow }

# --- 1. Controlli iniziali ---------------------------------------------------
Passo "Controllo prerequisiti"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "uv non trovato. Installalo con:  winget install astral-sh.uv" -ForegroundColor Red
    Write-Host "Poi chiudi e riapri PowerShell e rilancia lo script." -ForegroundColor Red
    exit 1
}
$uvPath = $uv.Source
Ok "uv trovato: $uvPath"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Avviso "FFmpeg non trovato (serve per le risposte parlate). Installalo con:  winget install Gyan.FFmpeg"
} else {
    Ok "FFmpeg trovato"
}

if (-not (Test-Path (Join-Path $cartella $FileAvvio))) {
    Write-Host "File $FileAvvio non trovato in $cartella. Esegui lo script dentro la cartella jarvis." -ForegroundColor Red
    exit 1
}
Ok "$FileAvvio trovato"

if (-not (Test-Path (Join-Path $cartella ".env"))) {
    Avviso ".env non trovato: copialo dall'altro PC prima di avviare Jarvis."
} else {
    Ok ".env presente"
}

# --- 2. Dipendenze -----------------------------------------------------------
Passo "Installo le dipendenze (uv sync)"
& $uvPath sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "uv sync e fallito." -ForegroundColor Red
    exit 1
}
Ok "Dipendenze installate"

# --- 3. Avvio automatico all'accesso -----------------------------------------
Passo "Creo l'avvio automatico (attivita pianificata '$NomeTask')"

$argomenti = "run $FileAvvio $ArgomentiAvvio".Trim()
$azione = New-ScheduledTaskAction -Execute $uvPath -Argument $argomenti -WorkingDirectory $cartella
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$impostazioni = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $NomeTask -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $NomeTask -Confirm:$false
}
Register-ScheduledTask -TaskName $NomeTask -Action $azione -Trigger $trigger `
    -Settings $impostazioni -Principal $principal | Out-Null
Ok "Jarvis partira da solo a ogni accesso a Windows"

# --- 4. Icona sul desktop ----------------------------------------------------
Passo "Creo l'icona sul desktop"

$desktop = [Environment]::GetFolderPath("Desktop")
$collegamento = Join-Path $desktop "Jarvis.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($collegamento)
$lnk.TargetPath = $uvPath
$lnk.Arguments = "run $FileAvvio mostra"
$lnk.WorkingDirectory = $cartella
$lnk.WindowStyle = 7   # finestra minimizzata
$lnk.Description = "Apri Jarvis"
$lnk.Save()
Ok "Icona creata: $collegamento (apre Jarvis con 'mostra')"

# --- 5. Alimentazione: mai in sospensione ------------------------------------
Passo "Imposto l'alimentazione su 'mai in sospensione'"

try {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /change standby-timeout-dc 0
    powercfg /change hibernate-timeout-dc 0
    Ok "Sospensione e ibernazione disattivate (rete e batteria). Lo schermo si spegne normalmente."
} catch {
    Avviso "Non sono riuscito a cambiare le impostazioni di alimentazione: $($_.Exception.Message)"
}

# --- Fine --------------------------------------------------------------------
Write-Host "`nInstallazione completata." -ForegroundColor Green
Write-Host "Ricorda: sull'altro PC (quello vecchio) disattiva Jarvis con:"
Write-Host "    Disable-ScheduledTask -TaskName Jarvis"
Write-Host "Per provarlo ora senza riavviare:  Start-ScheduledTask -TaskName $NomeTask"
