$ErrorActionPreference = "Stop"
Stop-ScheduledTask -TaskName "DevRadar Worker" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "DevRadar Worker" -Confirm:$false
Write-Host "DevRadar Worker removed. Data kept in $env:LOCALAPPDATA\devradar\"
