# Download broker history and evaluate recorded hypotheses. No paper daemon or orders.
[CmdletBinding()]
param(
    [ValidateSet('NIFTY','BANKNIFTY','SENSEX')][string]$Index = 'SENSEX',
    [Parameter(Mandatory=$true)][string]$Start,
    [Parameter(Mandatory=$true)][string]$End,
    [string]$Vault = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) '.secrets\growing-trader'),
    [string]$OutputDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) '.agent-state\owner-studies')
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install the project in its local .venv first.' }
if (-not (Test-Path -LiteralPath $OutputDirectory)) { New-Item -ItemType Directory -Path $OutputDirectory | Out-Null }
$taskStem = $Index + '-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [Guid]::NewGuid().ToString('N')
$taskData = Join-Path $OutputDirectory ($taskStem + '-history.json')
$taskResult = Join-Path $OutputDirectory ($taskStem + '-result.json')
Add-Type -AssemblyName System.Security.Cryptography.ProtectedData
$taskKeyBytes = $null
$taskSecretBytes = $null
$taskPayload = $null
try {
    $taskKeyBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes((Join-Path $Vault 'groww-api-key.dpapi')),
        $null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskSecretBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes((Join-Path $Vault 'groww-api-secret.dpapi')),
        $null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskPayload = @{
        api_key = [Text.Encoding]::UTF8.GetString($taskKeyBytes).Trim([char]0xFEFF).Trim()
        api_secret = [Text.Encoding]::UTF8.GetString($taskSecretBytes).Trim([char]0xFEFF).Trim()
    } | ConvertTo-Json -Compress
    $taskPayload | & $taskPython -I -m nifty_engine.agent_engine.owner_study download --credentials-stdin --index $Index --start $Start --end $End --output $taskData
    $taskExit = $LASTEXITCODE
} finally {
    if ($null -ne $taskKeyBytes) {[Array]::Clear($taskKeyBytes,0,$taskKeyBytes.Length)}
    if ($null -ne $taskSecretBytes) {[Array]::Clear($taskSecretBytes,0,$taskSecretBytes.Length)}
    $taskPayload = $null
}
if ($taskExit -ne 0) {exit $taskExit}
& $taskPython -I -m nifty_engine.agent_engine.owner_study evaluate --dataset $taskData --protocol (Join-Path $taskRoot 'config\owner_strategies.json') --output $taskResult
exit $LASTEXITCODE
