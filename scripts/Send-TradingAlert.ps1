# Independent PC notification. Mail credentials stay in the existing DPAPI vault.
[CmdletBinding()]
param([ValidateSet('VM_UNREACHABLE','WORKER_STUCK','INVALID_HEARTBEAT')][string]$Status,
      [ValidatePattern('^oracle-[0-9]+$')][string]$Incident)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$taskVault=Join-Path (Split-Path $taskRoot -Parent) '.secrets\growing-trader\report-mail.dpapi'
$taskBytes=$null
try {
    try { Add-Type -AssemblyName System.Security.Cryptography.ProtectedData }
    catch { Add-Type -AssemblyName System.Security }
    $taskBytes=[System.Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($taskVault),$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskMail=[Text.Encoding]::UTF8.GetString($taskBytes) | ConvertFrom-Json
    $taskPayload=@{key=$taskMail.key;sender=$taskMail.sender;recipient=$taskMail.recipient;status=$Status;incident=$Incident} | ConvertTo-Json -Compress
    $taskPayload | & (Join-Path $taskRoot '.venv\Scripts\python.exe') -I -m nifty_engine.agent_engine.vm_alert
} catch { Write-Output '{"status":"ALERT_TRANSPORT_UNAVAILABLE"}' }
finally {
    if ($null -ne $taskBytes) {[Array]::Clear($taskBytes,0,$taskBytes.Length)}
    $taskPayload=$null;$taskMail=$null
}
