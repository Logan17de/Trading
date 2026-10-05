# Desktop app entry point. The engine/server starts hidden; Edge shows only its app window.
[CmdletBinding()]
param([switch]$BackgroundOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskHash = [Security.Cryptography.SHA256]::Create()
try { $taskName = [BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot))).Replace('-','').Substring(0,16) }
finally { $taskHash.Dispose() }
$taskMutex = New-Object Threading.Mutex($false,('Local\OptionsTrader-' + $taskName))
$taskAcquired = $false
$taskArgs = @{ Background = $true; App = $true }
if ($BackgroundOnly) { $taskArgs.NoBrowser = $true }
try {
    try { $taskAcquired = $taskMutex.WaitOne(15000) } catch [Threading.AbandonedMutexException] { $taskAcquired = $true }
    if (-not $taskAcquired) { throw 'Options Trader startup is already in progress.' }
    & (Join-Path $PSScriptRoot 'Start-TradingDashboard.ps1') @taskArgs
} finally { if ($taskAcquired) { $taskMutex.ReleaseMutex() }; $taskMutex.Dispose() }
