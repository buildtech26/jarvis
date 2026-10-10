<#
Riavvia Jarvis nel modo giusto e controlla che sia ripartito.

    powershell -NoProfile -ExecutionPolicy Bypass -File riavvia.ps1             finestra aperta, come l'icona
    powershell -NoProfile -ExecutionPolicy Bypass -File riavvia.ps1 -DaTelegram finestra nascosta (Alt+J)

Chiude TUTTE le copie di main.py: Stop-ScheduledTask ferma solo quella partita dall'attività, non quella aperta
dall'icona del desktop (modo=mostra), e la nuova copia scriveva "Jarvis è già in esecuzione" lasciando attivo il
codice vecchio. Riparte con il pythonw.exe di uv (vedi l'inizio di main.py), poi aspetta nel log la riga
"Bot Telegram attivo" o un traceback. Con -DaTelegram un riavvio fallito viene segnalato su Telegram: la conferma
di un riavvio riuscito la manda invece il bot appena ripartito (riavvio.json).
#>
param([switch]$DaTelegram)

$cartella = $PSScriptRoot
$main = Join-Path $cartella 'main.py'
$log = Join-Path $cartella 'jarvis.log'
$casa = ((Get-Content (Join-Path $cartella '.venv\pyvenv.cfg')) -match '^home\s*=')[0] -replace '^home\s*=\s*', ''
$pythonw = Join-Path $casa 'pythonw.exe'

function Avvisa([string]$testo) {
    Write-Output $testo
    if (-not $DaTelegram) { return }
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
