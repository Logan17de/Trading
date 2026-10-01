# A bounded read-only recording, using one token in one child process.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Until,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [ValidateRange(30,300)][int]$IntervalSeconds = 60,
    [string]$Vault = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) '.secrets\growing-trader')
)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {throw 'Install the local .venv first.'}
Add-Type -AssemblyName System.Security.Cryptography.ProtectedData
$taskKeyBytes=$null
$taskSecretBytes=$null
$taskPayload=$null
try {
    $taskKeyBytes=[System.Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes((Join-Path $Vault 'groww-api-key.dpapi')),$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskSecretBytes=[System.Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes((Join-Path $Vault 'groww-api-secret.dpapi')),$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskPayload=@{
        api_key=[Text.Encoding]::UTF8.GetString($taskKeyBytes).Trim([char]0xFEFF).Trim()
        api_secret=[Text.Encoding]::UTF8.GetString($taskSecretBytes).Trim([char]0xFEFF).Trim()
    } | ConvertTo-Json -Compress
    $taskPayload | & $taskPython -I -m nifty_engine.agent_engine.owner_study record --credentials-stdin --until $Until --interval-seconds $IntervalSeconds --output $OutputDirectory
    $taskExit=$LASTEXITCODE
} finally {
    if ($null -ne $taskKeyBytes) {[Array]::Clear($taskKeyBytes,0,$taskKeyBytes.Length)}
    if ($null -ne $taskSecretBytes) {[Array]::Clear($taskSecretBytes,0,$taskSecretBytes.Length)}
    $taskPayload=$null
}
exit $taskExit
