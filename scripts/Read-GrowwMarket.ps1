# Local read-only diagnostic. Secrets stay in DPAPI storage and a process stdin pipe.
[CmdletBinding()]
param(
    [switch]$Dashboard,
    [switch]$Watch,
    [string]$LeaseFile,
    [string]$Vault,
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
# Windows PowerShell 5.1 can evaluate parameter defaults before PSScriptRoot exists.
if ([string]::IsNullOrWhiteSpace($Vault)) { $Vault = Join-Path (Split-Path $taskRoot -Parent) '.secrets\growing-trader' }
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path $taskRoot '.agent-state\market-checks' }
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install the project in its local .venv first.' }
if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
}
try { Add-Type -AssemblyName System.Security.Cryptography.ProtectedData }
catch { Add-Type -AssemblyName System.Security } # Windows PowerShell 5.1
$taskKeyBytes = $null
$taskSecretBytes = $null
$taskPayload = $null
try {
    $taskKeyBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        [System.IO.File]::ReadAllBytes((Join-Path $Vault 'groww-api-key.dpapi')),
        $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskSecretBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        [System.IO.File]::ReadAllBytes((Join-Path $Vault 'groww-api-secret.dpapi')),
        $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskPayload = @{
        api_key = [Text.Encoding]::UTF8.GetString($taskKeyBytes).Trim([char]0xFEFF).Trim()
        api_secret = [Text.Encoding]::UTF8.GetString($taskSecretBytes).Trim([char]0xFEFF).Trim()
    } | ConvertTo-Json -Compress
    $taskName = 'market-check-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [Guid]::NewGuid().ToString('N') + '.json'
    $taskOutput = Join-Path $OutputDirectory $taskName
    if ($Dashboard -and $Watch) {
        $taskPayload | & $taskPython -I -m nifty_engine.agent_engine.dashboard watch --credentials-stdin --directory $OutputDirectory --lease-file $LeaseFile --root $taskRoot
    } elseif ($Dashboard) {
        $taskPayload | & $taskPython -I -m nifty_engine.agent_engine.dashboard capture --credentials-stdin --output $taskOutput
    } else {
        $taskPayload | & $taskPython -I -m nifty_engine.agent_engine.market_check --credentials-stdin --output $taskOutput
    }
    $taskExit = $LASTEXITCODE
} finally {
    if ($null -ne $taskKeyBytes) { [Array]::Clear($taskKeyBytes, 0, $taskKeyBytes.Length) }
    if ($null -ne $taskSecretBytes) { [Array]::Clear($taskSecretBytes, 0, $taskSecretBytes.Length) }
    $taskPayload = $null
}
exit $taskExit
