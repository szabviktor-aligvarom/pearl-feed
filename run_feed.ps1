# =====================================================================
#  pearl.de feed - Windows futtato script
#  3 naponta 22:00-kor fut (Windows Task Scheduler inditja)
#
#  Amit tesz:
#   1. legfrissebb allapot lehuzasa a GitHub repobol
#   2. build_feed.py futtatasa (letoltes + JSON/CSV epites)
#   3. siker esetén commit + push  -> az allando link frissul
#   4. hiba esetén NEM pushol, es emailt kuld
#   5. minden futasrol "heartbeat" jelzest ir a repoba (a gepleallas figyelesehez)
# =====================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# --- BEALLITASOK ---------------------------------------------------
$RepoDir   = "C:\pearl-feed"                     # a repo helye a gepen
$PythonExe = "python"                            # ha kell: C:\Python312\python.exe
$LogDir    = Join-Path $RepoDir "logs"
$Stamp     = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = Join-Path $LogDir "run_$Stamp.log"

# Email ertesites (a jelszot a Windows kornyezeti valtozobol olvassuk,
# hogy ne legyen a fajlban: setx PEARL_MAIL_PASS "..." )
$MailFrom  = $env:PEARL_MAIL_FROM
$MailTo    = $env:PEARL_MAIL_TO
$MailPass  = $env:PEARL_MAIL_PASS
$SmtpHost  = "smtp.gmail.com"
$SmtpPort  = 587

# ------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Send-Alert($subject, $body) {
    if (-not $MailFrom -or -not $MailTo -or -not $MailPass) {
        Log "FIGYELEM: email kuldes kihagyva, nincsenek beallitva a PEARL_MAIL_* kornyezeti valtozok."
        return
    }
    try {
        $sec = ConvertTo-SecureString $MailPass -AsPlainText -Force
        $cred = New-Object System.Management.Automation.PSCredential($MailFrom, $sec)
        Send-MailMessage -From $MailFrom -To $MailTo -Subject $subject -Body $body `
            -SmtpServer $SmtpHost -Port $SmtpPort -UseSsl -Credential $cred -Encoding UTF8
        Log "Ertesito email elkuldve: $MailTo"
    } catch {
        Log "HIBA: az email kuldes nem sikerult: $($_.Exception.Message)"
    }
}

$startTime = Get-Date
Log "=== pearl.de feed futas indul ==="

try {
    Set-Location $RepoDir

    Log "Git pull..."
    & git pull --rebase --quiet 2>&1 | ForEach-Object { Log "  git: $_" }

    Log "Feed epites indul (ez 1-3 orat is igenybe vehet)..."
    & $PythonExe "build_feed.py" 2>&1 | ForEach-Object { Log "  $_" }
    $exit = $LASTEXITCODE

    if ($exit -eq 0) {
        Log "A feed sikeresen elkeszult. Feltoltes a GitHubra..."

        # heartbeat: mikor futott le utoljara SIKERESEN
        $hb = @{
            utolso_sikeres_futas = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
            gep                  = $env:COMPUTERNAME
            futasi_ido_perc      = [math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)
            allapot              = "ok"
        } | ConvertTo-Json
        Set-Content -Path (Join-Path $RepoDir "feed\heartbeat.json") -Value $hb -Encoding UTF8

        & git add feed/ 2>&1 | Out-Null
        $msg = "feed frissites {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm")
        & git commit -m $msg 2>&1 | ForEach-Object { Log "  git: $_" }
        & git push --quiet 2>&1 | ForEach-Object { Log "  git: $_" }
        Log "=== KESZ: a feed kint van az allando linken ==="
    }
    else {
        Log "A feed epites HIBAVAL leallt (exit=$exit). A regi feed marad kint, nincs push."
        $tail = (Get-Content $LogFile -Tail 40) -join "`r`n"
        Send-Alert "[pearl-feed] HIBA: a feed frissites elvérzett" @"
A pearl.de feed frissitese hibaval leallt.

Gep: $env:COMPUTERNAME
Idopont: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Exit kod: $exit

FONTOS: az elozo, jo feed valtozatlanul kint van a linken, tehat a shop nem kapott rossz adatot.

A log utolso sorai:
$tail
"@
        exit $exit
    }
}
catch {
    Log "VARATLAN HIBA: $($_.Exception.Message)"
    Send-Alert "[pearl-feed] VARATLAN HIBA a futas alatt" @"
A pearl.de feed futasa varatlan hibaval leallt.

Gep: $env:COMPUTERNAME
Idopont: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Hiba: $($_.Exception.Message)

Az elozo, jo feed valtozatlanul kint van a linken.
"@
    exit 1
}
