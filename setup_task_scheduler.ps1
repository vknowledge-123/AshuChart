# PowerShell Script to Setup Windows Task Scheduler for Daily Cleanup
# Run this script as Administrator to create the scheduled task

$TaskName = "AlgoEdge_DailyCleanup"
$ScriptPath = Join-Path $PSScriptRoot "schedule_daily_cleanup.bat"
$Description = "Daily cleanup of trading data at 8:00 AM IST"

# Check if task already exists
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($ExistingTask) {
    Write-Host "⚠️  Task already exists. Removing old task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create trigger for 8:00 AM daily
$Trigger = New-ScheduledTaskTrigger -Daily -At "08:00"

# Create action to run batch file
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$ScriptPath`""

# Create principal (run whether user is logged in or not)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

# Create settings
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -DontStopOnIdleEnd

# Register the task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Principal $Principal `
    -Settings $Settings `
    -Description $Description

Write-Host "✅ Task Scheduler setup complete!" -ForegroundColor Green
Write-Host "" 
Write-Host "Task Details:" -ForegroundColor Cyan
Write-Host "  Name: $TaskName"
Write-Host "  Time: 8:00 AM (Daily)"
Write-Host "  Script: $ScriptPath"
Write-Host ""
Write-Host "To verify, run: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
