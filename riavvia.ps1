<#
Riavvia Jarvis nel modo giusto e controlla che sia ripartito.

    powershell -NoProfile -ExecutionPolicy Bypass -File riavvia.ps1             finestra aperta, come l'icona
    powershell -NoProfile -ExecutionPolicy Bypass -File riavvia.ps1 -DaTelegram finestra nascosta (Alt+J)
    ... -SenzaProve                                                             salta le prove (emergenza)

Prima di chiudere qualcosa lancia pyflakes e le prove offline (prova_cervello, prova_gruppi, prova_verifica,
prova_calendario, prova_allenamento, prova_alimentazione): se
una fallisce, la copia in esecuzione resta accesa con il codice di prima e l'errore arriva su Telegram. Il 10/10 un
import mancante in brain.py faceva fallire ogni domanda con strumenti, e Jarvis rispondeva "Gemini non risponde".

Chiude TUTTE le copie di main.py: Stop-ScheduledTask ferma solo quella partita dall'attività, non quella aperta
dall'icona del desktop (modo=mostra), e la nuova copia scriveva "Jarvis è già in esecuzione" lasciando attivo il
codice vecchio. La copia "servizio" avviata da "Jarvis sorveglia" gira nella sessione 0 e da qui non si vede
(CommandLine vuota): "Chiuse 0 copie" è normale, la copia nuova le chiede "cedi" e prende il suo posto (provato il
14/10). Riparte con il pythonw.exe di uv (vedi l'inizio di main.py), poi aspetta nel log la riga
"Bot Telegram attivo" o un traceback. Con -DaTelegram un riavvio fallito viene segnalato su Telegram: la conferma
di un riavvio riuscito la manda invece il bot appena ripartito (riavvio.json).
#>
param([switch]$DaTelegram, [switch]$SenzaProve)

$cartella = $PSScriptRoot
$main = Join-Path $cartella 'main.py'
$log = Join-Path $cartella 'jarvis.log'
$casa = ((Get-Content (Join-Path $cartella '.venv\pyvenv.cfg')) -match '^home\s*=')[0] -replace '^home\s*=\s*', ''
$pythonw = Join-Path $casa 'pythonw.exe'

# -Sempre: su Telegram anche quando il riavvio parte dalla finestra (le prove fallite non si vedrebbero altrove)
function Avvisa([string]$testo, [switch]$Sempre) {
    Write-Output $testo
    if (-not ($DaTelegram -or $Sempre)) { return }
    if ($testo.Length -gt 3900) { $testo = $testo.Substring(0, 3900) + "`n…" }  # Telegram accetta 4096 caratteri
    $env_ = @{}
    foreach ($riga in Get-Content (Join-Path $cartella '.env')) {
        if ($riga -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { $env_[$Matches[1]] = $Matches[2].Trim().Trim('"') }
    }
    $corpo = @{ chat_id = $env_['TELEGRAM_UTENTE']; text = $testo } | ConvertTo-Json
    try {
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$($env_['TELEGRAM_TOKEN'])/sendMessage" `
            -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($corpo)) | Out-Null
    } catch { }
}

if (-not (Test-Path $pythonw)) {
    Avvisa "Riavvio fallito: non trovo $pythonw (uv ha aggiornato Python? rilancia crea_icona.py)."
    exit 1
}

# 0. prove offline, prima di toccare la copia che funziona
if (-not $SenzaProve) {
    Push-Location $cartella
    $env:PYTHONIOENCODING = 'utf-8'
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }  # accenti giusti nell'avviso
    $problemi = @()
    $uvx = Get-Command uvx -ErrorAction SilentlyContinue
    if ($uvx) {
        # solo gli errori veri: un import inutilizzato non deve impedire il riavvio
        $gravi = @(& $uvx.Source pyflakes (Get-ChildItem -Filter *.py).Name 2>&1 | ForEach-Object { "$_" } |
            Where-Object { $_ -match 'undefined name|referenced before assignment|syntax|indent|invalid|unexpected|expected' })
        if ($gravi) { $problemi += "pyflakes:`n" + ($gravi -join "`n") }
    } else {
        Write-Output "uvx non trovato: salto pyflakes."
    }
    foreach ($prova in 'prova_cervello.py', 'prova_gruppi.py', 'prova_verifica.py', 'prova_calendario.py',
                       'prova_allenamento.py', 'prova_alimentazione.py', 'prova_dispensa.py', 'prova_spesa.py',
                       'prova_salute.py', 'prova_controllo.py', 'prova_rete.py', 'prova_backup.py', 'prova_web.py') {
        $uscita = @(& (Join-Path $cartella '.venv\Scripts\python.exe') $prova 2>&1 | ForEach-Object { "$_" })
        if ($LASTEXITCODE -ne 0) { $problemi += "${prova}:`n" + (($uscita | Select-Object -Last 10) -join "`n") }
    }
    Pop-Location
    if ($problemi) {
        # scritto da /riavvia su Telegram: lasciato lì, il prossimo avvio confermerebbe un riavvio mai avvenuto
        try { Remove-Item (Join-Path $cartella 'riavvio.json') -ErrorAction Stop } catch { }
        Avvisa -Sempre ("Riavvio annullato: Jarvis resta acceso con il codice di prima. Prove fallite:`n`n" +
            ($problemi -join "`n`n"))
        exit 1
    }
    Write-Output "Prove superate."
}

# 1. chiude tutte le copie
$vecchi = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -like "*$main*" })
foreach ($p in $vecchi) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($p in $vecchi) { Wait-Process -Id $p.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }
Write-Output "Chiuse $($vecchi.Count) copie di Jarvis."

# 2. riparte
$righe_prima = @(Get-Content $log -Encoding utf8 -ErrorAction SilentlyContinue).Count
$modo = if ($DaTelegram) { 'tutto' } else { 'mostra' }
Start-Process -FilePath $pythonw -ArgumentList "`"$main`" $modo --log" -WorkingDirectory $cartella

# 3. aspetta l'esito nel log (l'avvio dura fino a un minuto)
$scadenza = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $scadenza) {
    Start-Sleep -Seconds 2
    $nuove = @(Get-Content $log -Encoding utf8 -ErrorAction SilentlyContinue | Select-Object -Skip $righe_prima)
    $testo = $nuove -join "`n"
    if ($testo -match 'Traceback|già in esecuzione') {
        Avvisa ("Riavvio fallito, ultime righe del log:`n" + (($nuove | Select-Object -Last 12) -join "`n"))
        exit 1
    }
    if ($testo -match 'Bot Telegram attivo|Telegram non configurato') {
        Write-Output "Jarvis è ripartito (modo=$modo)."
        exit 0
    }
}
Avvisa "Riavvio incerto: dopo 2 minuti nel log non c'è ancora 'Bot Telegram attivo'."
exit 1
