# Local read-only diagnostic. Secrets stay in DPAPI storage and a process stdin pipe.
[CmdletBinding()]
param(
    [string]$Vault = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) '.secrets\growing-trader'),
    [string]$OutputDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) '.agent-state\market-checks')
)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install the project in its local .venv first.' }
if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
}
Add-Type -AssemblyName System.Security.Cryptography.ProtectedData
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
    $taskPayload | & $taskPython -I -m nifty_engine.agent_engine.market_check --credentials-stdin --output $taskOutput
    $taskExit = $LASTEXITCODE
} finally {
    if ($null -ne $taskKeyBytes) { [Array]::Clear($taskKeyBytes, 0, $taskKeyBytes.Length) }
    if ($null -ne $taskSecretBytes) { [Array]::Clear($taskSecretBytes, 0, $taskSecretBytes.Length) }
    $taskPayload = $null
}
exit $taskExit
