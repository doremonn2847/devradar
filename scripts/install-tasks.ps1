# Registers the DevRadar worker to start hidden at logon for the current user. No admin needed.
$ErrorActionPreference = "Stop"

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { throw "pythonw.exe not found on PATH. Install Python 3.12+ from python.org." }
# Check with the same interpreter the task will run (python.exe may be a different install or the Store alias).
$pythonExe = Join-Path (Split-Path $pythonw) "python.exe"
& $pythonExe -c "import devradar" 2>$null
if ($LASTEXITCODE -ne 0) { throw "devradar is not installed for $pythonExe. Run: `"$pythonExe`" -m pip install -e ." }
if (-not [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")) {
    Write-Warning "DEEPSEEK_API_KEY is not set for your user. Run: setx DEEPSEEK_API_KEY sk-..."
}

$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "-m devradar.worker"
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

Register-ScheduledTask -TaskName "DevRadar Worker" -Action $action -Trigger $trigger `
    -Settings $settings -Description "DevRadar: session collector, rules and news" -Force | Out-Null
Start-ScheduledTask -TaskName "DevRadar Worker"
Write-Host "DevRadar Worker registered and started. Log: $env:LOCALAPPDATA\devradar\worker.log"
