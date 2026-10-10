# heartbeat_status.ps1 - is the Moltbook heartbeat running?
# Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\heartbeat_status.ps1

param([string]$TaskName = "Moltbook Heartbeat")
$folder = Split-Path -Parent $MyInvocation.MyCommand.Path

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "NOT INSTALLED: run .\install_heartbeat_task.ps1 first." -ForegroundColor Red
    exit 1
}
$info = Get-ScheduledTaskInfo -TaskName $TaskName

$resultText = switch ($info.LastTaskResult) {
    0          { "OK" }
    267009     { "running right now" }
    267011     { "has not run yet" }
    2147946720 { "skipped: previous run still going" }
    default    { "error code $($info.LastTaskResult)" }
}

Write-Host ""
Write-Host "Task      : $TaskName"
Write-Host "State     : $($task.State)" -ForegroundColor $(if ($task.State -eq "Disabled") { "Red" } else { "Green" })
Write-Host "Last run  : $($info.LastRunTime)   result: $resultText"
Write-Host "Next run  : $($info.NextRunTime)"

# Is Ollama up? (the heartbeat's brain)
try {
    Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null
    Write-Host "Ollama    : running" -ForegroundColor Green
} catch {
    Write-Host "Ollama    : NOT running - start the Ollama app" -ForegroundColor Red
}

# Is a heartbeat running right now?
$lock = Join-Path $folder "heartbeat.lock"
if (Test-Path $lock) {
    $age = [int]((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes
    Write-Host "Now       : a heartbeat is in progress (started $age min ago)"
}

# Safety stop status
$fails = Join-Path $folder "moltbook_failures.json"
if (Test-Path $fails) {
    $n = (Get-Content $fails -Raw | ConvertFrom-Json).consecutive_failures
    $colour = if ($n -ge 3) { "Red" } elseif ($n -gt 0) { "Yellow" } else { "Green" }
    Write-Host "Maths     : $n failed check(s) in a row (pauses at 3)" -ForegroundColor $colour
}

$log = Join-Path $folder "heartbeat.log"
if (Test-Path $log) {
    Write-Host ""
    Write-Host "Last heartbeat results:"
    Get-Content $log -Tail 400 | Select-String "Heartbeat done|crashed|NEEDS YOU|paused|rejected" |
        Select-Object -Last 6 | ForEach-Object { Write-Host "  $($_.Line)" }
}
Write-Host ""
