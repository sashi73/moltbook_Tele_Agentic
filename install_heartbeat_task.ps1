# install_heartbeat_task.ps1
# Creates (or updates) a Windows scheduled task that runs one Moltbook heartbeat
# every 2 hours, silently in the background (no console window).
#
# Run from the project folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\install_heartbeat_task.ps1
# Options:
#   -EveryMinutes 60     run every 60 minutes instead of every 120 (2 hours)
#   -ShowWindow          use the .bat with a visible console (for troubleshooting)
#   -WakeToRun           wake the PC from sleep to run (laptops: uses battery)
#
# Check status any time:   powershell -ExecutionPolicy Bypass -File .\heartbeat_status.ps1
# Remove the task:         Unregister-ScheduledTask -TaskName "Moltbook Heartbeat" -Confirm:$false

param(
    [int]$EveryMinutes = 120,
    [string]$TaskName = "Moltbook Heartbeat",
    [switch]$ShowWindow,
    [switch]$WakeToRun
)

$folder = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $folder "heartbeat.py"))) {
    Write-Error "heartbeat.py not found in $folder"; exit 1
}

if ($ShowWindow) {
    $action = New-ScheduledTaskAction -Execute (Join-Path $folder "run_heartbeat.bat") -WorkingDirectory $folder
    $mode = "visible console window"
} else {
    # pythonw.exe = Python without a console window. Find the one next to python.exe.
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    $pyw = if ($py) { Join-Path (Split-Path $py) "pythonw.exe" } else { $null }
    if (-not $pyw -or -not (Test-Path $pyw)) {
        $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    }
    if (-not $pyw -or -not (Test-Path $pyw)) {
        Write-Error "pythonw.exe not found. Re-run with -ShowWindow, or check your Python install."; exit 1
    }
    $action = New-ScheduledTaskAction -Execute $pyw -Argument "heartbeat.py" -WorkingDirectory $folder
    $mode = "hidden (pythonw: $pyw)"
}

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
           -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)

$settingsArgs = @{
    MultipleInstances          = "IgnoreNew"
    ExecutionTimeLimit         = (New-TimeSpan -Minutes 25)
    StartWhenAvailable         = $true     # runs a missed heartbeat after sleep/shutdown
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    RestartCount               = 2
    RestartInterval            = (New-TimeSpan -Minutes 5)
}
if ($WakeToRun) { $settingsArgs["WakeToRun"] = $true }
$settings = New-ScheduledTaskSettingsSet @settingsArgs

# Runs as you while you are logged in, so it can reach the Ollama app.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Moltbook presence heartbeat for gandhilearner" -Force | Out-Null

Write-Host ""
Write-Host "Scheduled '$TaskName' every $EveryMinutes minutes, first run in about 1 minute."
Write-Host "Mode: $mode"
Write-Host "You will get a Telegram report after each run. Log: $folder\heartbeat.log"
Write-Host "Check status: powershell -ExecutionPolicy Bypass -File .\heartbeat_status.ps1"
