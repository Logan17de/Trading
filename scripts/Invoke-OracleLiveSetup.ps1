# Owner-operated only. This script is never called by deployment or Algo Start.
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('preview','submit','capture','status','close','cancel','arm-preview','arm')][string]$Action,
  [string]$SpecFile,
  [ValidatePattern('^[a-f0-9]{32}$')][string]$PlanId,
  [ValidatePattern('^[a-f0-9]{64}$')][string]$Confirm
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$taskConfig=Get-Content -LiteralPath (Join-Path $taskRoot '.agent-state/oracle-viewer.json') -Raw | ConvertFrom-Json
if ($taskConfig.user -ne 'ubuntu' -or $taskConfig.root -ne '/var/lib/trading-observer' -or
    $taskConfig.host -notmatch '^[a-zA-Z0-9.-]{1,253}$' -or
    $taskConfig.python -notmatch '^/opt/growing-trader/releases/[a-f0-9]{40}/venv/bin/python$') {
    throw 'Reviewed Oracle connection required'
}
$taskCommand="sudo -n $($taskConfig.python) -I -m nifty_engine.agent_engine.owner_setup "
$taskInput=''
if ($Action -eq 'preview') {
    if (-not $SpecFile) {throw 'Provide a private JSON test plan with -SpecFile'}
    $taskPlanPath=(Resolve-Path -LiteralPath $SpecFile).Path
    $taskPrivateRoot=[IO.Path]::GetFullPath((Join-Path $taskRoot '.agent-state'))+[IO.Path]::DirectorySeparatorChar
    if (-not $taskPlanPath.StartsWith($taskPrivateRoot,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Keep the private test plan under Trading/.agent-state'
    }
    $taskInput=[IO.File]::ReadAllText($taskPlanPath,[Text.Encoding]::UTF8)
    if ($taskInput.Length -gt 8192) {throw 'Test plan too large'}
    $null=$taskInput | ConvertFrom-Json
    $taskCommand+='preview-stdin'
} else {
    if (-not $PlanId) {throw 'Use the exact PlanId returned by preview'}
    $taskCommand+="$Action --id $PlanId"
    if ($Action -in @('submit','close','cancel','arm')) {
        if (-not $Confirm) {throw 'Provide the exact plan hash (or arm evidence digest) using -Confirm'}
        $taskCommand+=" --confirm $Confirm"
    }
}
$taskArgs=@('-F','NUL','-T','-i',$taskConfig.identity_file,'-o','BatchMode=yes',
  '-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','IdentityAgent=none',
  '-o','ClearAllForwardings=yes','-o','ConnectTimeout=15',"ubuntu@$($taskConfig.host)",$taskCommand)
$taskInput | & C:/Windows/System32/OpenSSH/ssh.exe @taskArgs
if ($LASTEXITCODE -ne 0) {throw 'Owner setup did not complete. Read its status before retrying; never submit a replacement plan to recover an uncertain order.'}
