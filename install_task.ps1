# =====================================================================
#  Windows utemezett feladat letrehozasa a pearl.de feedhez
#  Futtatas: Rendszergazdaként indított PowerShell-ben
#      cd C:\pearl-feed
#      .\install_task.ps1
# =====================================================================

#Requires -RunAsAdministrator

$TaskName = "Pearl feed frissites"
$RepoDir  = "C:\pearl-feed"
$Script   = Join-Path $RepoDir "run_feed.ps1"

if (-not (Test-Path $Script)) {
    Write-Host "HIBA: nem talalom a run_feed.ps1-et itt: $Script" -ForegroundColor Red
    Write-Host "Eloszor klonozd a repot a $RepoDir mappaba." -ForegroundColor Yellow
    exit 1
}

# Ha mar letezik, toroljuk
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "A(z) '$TaskName' feladat mar letezik, felulirjuk..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $RepoDir

# 3 naponta 22:00 - a mai naptol indul
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval 3 -At "22:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -MultipleInstances IgnoreNew

# -StartWhenAvailable: ha a gep 22:00-kor le volt allva, a bekapcsolas utan potolja

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "A pearl.de ar- es keszletfeed frissitese, 3 naponta 22:00-kor. Hiba eseten emailt kuld es nem irja felul a jo adatot." | Out-Null

Write-Host ""
Write-Host "KESZ: a(z) '$TaskName' feladat letrejott." -ForegroundColor Green
Write-Host ""
Write-Host "Utemezes:  3 naponta 22:00" -ForegroundColor Cyan
Write-Host "Potlas:    ha a gep le volt allva, bekapcsolas utan automatikusan lefut" -ForegroundColor Cyan
Write-Host ""

# Ellenorzes, hogy az email valtozok be vannak-e allitva
$missing = @()
foreach ($v in @("PEARL_MAIL_FROM","PEARL_MAIL_TO","PEARL_MAIL_PASS")) {
    if (-not [Environment]::GetEnvironmentVariable($v, "User")) { $missing += $v }
}
if ($missing.Count -gt 0) {
    Write-Host "FIGYELEM: az email ertesiteshez meg be kell allitani ezeket:" -ForegroundColor Yellow
    foreach ($m in $missing) { Write-Host "    setx $m `"...`"" -ForegroundColor Yellow }
    Write-Host ""
}

Write-Host "Kezi probafutas most:" -ForegroundColor White
Write-Host "    Start-ScheduledTask -TaskName `"$TaskName`"" -ForegroundColor White
Write-Host ""
