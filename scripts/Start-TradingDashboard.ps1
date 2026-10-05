# Start the local viewer. This does not activate the trading engine or Oracle services.
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port = 8765,
    [switch]$Offline,
    [switch]$Background,
    [switch]$App,
    [switch]$Stop,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install the project in its local .venv first.' }
$taskUrl = "http://127.0.0.1:$Port"
$taskState = Join-Path $taskRoot '.agent-state'
if (-not (Test-Path -LiteralPath $taskState)) { New-Item -ItemType Directory -Path $taskState | Out-Null }
if ($Stop) {
    $taskPidPath = Join-Path $taskState "dashboard-$Port.pid"
    if (-not (Test-Path -LiteralPath $taskPidPath)) { throw 'No recorded local dashboard process.' }
    $taskViewerId = [int](Get-Content -LiteralPath $taskPidPath)
    $taskViewer = Get-CimInstance Win32_Process -Filter "ProcessId=$taskViewerId"
    if ($null -ne $taskViewer) {
        if ($taskViewer.CommandLine -notmatch 'nifty_engine\.agent_engine\.dashboard.*serve' -or
            -not $taskViewer.CommandLine.Contains($taskRoot)) { throw 'Dashboard process identity not confirmed.' }
        Stop-Process -Id $taskViewerId
    }
    Write-Output 'Local viewer stopped. Its read-only collector stops when the viewer lease expires.'
    return
}
$taskAlreadyRunning = $false
try {
    $taskCheck = Invoke-RestMethod -Uri ($taskUrl + '/api/dashboard') -TimeoutSec 2
    $taskAlreadyRunning = $taskCheck.format -eq 'trading-dashboard-v1'
} catch { }
if ($taskAlreadyRunning -and [bool]$taskCheck.offline -ne [bool]$Offline) {
    throw 'Dashboard is running in a different viewing mode. Stop it with -Stop, then restart.'
}
if ($taskAlreadyRunning -and $Background -and -not $taskCheck.background_monitor) {
    & $PSCommandPath -Port $Port -Stop
    $taskAlreadyRunning = $false
    for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
        try { $null = Invoke-RestMethod -Uri ($taskUrl + '/api/dashboard') -TimeoutSec 1 } catch { break }
        Start-Sleep -Milliseconds 100
    }
}
if (-not $taskAlreadyRunning) {
    $taskArguments = @('-I','-m','nifty_engine.agent_engine.dashboard','serve','--port',"$Port",'--root',('"' + $taskRoot + '"'))
    if ($Offline) { $taskArguments += '--offline' }
    if ($Background) { $taskArguments += '--background' }
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskState "dashboard-$Port.stdout.log") `
        -RedirectStandardError (Join-Path $taskState "dashboard-$Port.stderr.log")
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
        if ($taskProcess.HasExited) { throw 'Dashboard could not start. Check its private log for a sanitized status.' }
        try {
            $taskCheck = Invoke-RestMethod -Uri ($taskUrl + '/api/dashboard') -TimeoutSec 2
            if ($taskCheck.format -eq 'trading-dashboard-v1') { $taskReady = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $taskReady) {
        # Do not leave a failed, unrecorded server holding the local port.
        $taskStarted = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskProcess.Id) OR ParentProcessId=$($taskProcess.Id)"
        foreach ($taskCandidate in $taskStarted) {
            if ($taskCandidate.CommandLine -match 'nifty_engine\.agent_engine\.dashboard.*serve' -and
                $taskCandidate.CommandLine.Contains($taskRoot)) { Stop-Process -Id $taskCandidate.ProcessId -ErrorAction SilentlyContinue }
        }
        throw 'Local dashboard did not become ready.'
    }
    $taskProcess.Id | Set-Content -LiteralPath (Join-Path $taskState "dashboard-$Port.pid")
}
if (-not $NoBrowser) {
    if ($App) { Start-Process -FilePath 'msedge.exe' -ArgumentList ('--app=' + $taskUrl) }
    else { Start-Process -FilePath 'msedge.exe' -ArgumentList $taskUrl }
}
Write-Output "Options Trader: $taskUrl (read-only local view)"
