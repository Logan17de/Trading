# Reviewed read-only deployment. Never starts the trader or changes the pause.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Commit,
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9.-]+$')][string]$HostName,
      [Parameter(Mandatory=$true)][string]$IdentityFile)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$taskRelease="/opt/growing-trader/releases/$Commit"
$taskSshArgs=@('-F','NUL','-T','-i',$IdentityFile,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15',"ubuntu@$HostName")
$taskSsh='C:\Windows\System32\OpenSSH\ssh.exe'
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
# Source archive contains only this exact committed revision, never the vault/state.
$taskArchive=Join-Path $taskRoot ".agent-state\observer-$Commit.tar"
& git -C $taskRoot archive --format=tar --output=$taskArchive $Commit
if ($LASTEXITCODE -ne 0) {throw 'Source archive failed'}
& $taskSsh @taskSshArgs "sudo -n mkdir -p $taskRelease"
if ($LASTEXITCODE -ne 0) {throw 'Release preparation failed'}
$taskArchiveScript=@'
import subprocess,sys
from pathlib import Path
args=["C:/Windows/System32/OpenSSH/ssh.exe","-F","NUL","-T","-i",sys.argv[2],"-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","ubuntu@"+sys.argv[3],"sudo -n tar -xf - -C /opt/growing-trader/releases/"+sys.argv[4]]
with open(sys.argv[1],"rb") as f:r=subprocess.run(args,stdin=f,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
raise SystemExit(r.returncode)
'@
$taskArchiveScript | & $taskPython - $taskArchive $IdentityFile $HostName $Commit
if ($LASTEXITCODE -ne 0) {throw 'Release transfer failed'}
$taskSetup=@'
import json,os,pathlib,pwd,subprocess,sys
release=pathlib.Path('/opt/growing-trader/releases')/sys.argv[1]
state=pathlib.Path('/var/lib/trading-observer')
try:owner=pwd.getpwnam('trading-observer')
except KeyError:
 subprocess.run(['useradd','--system','--home',str(state),'--shell','/usr/sbin/nologin','trading-observer'],check=True)
 owner=pwd.getpwnam('trading-observer')
state.mkdir(mode=0o700,exist_ok=True);os.chown(state,owner.pw_uid,owner.pw_gid)
config=state/'config'
if config.exists() or config.is_symlink():
 if not config.is_symlink():raise ValueError('unexpected state config')
 config.unlink()
config.symlink_to(release/'config',target_is_directory=True)
pause=state/'.trader-paused'
if not pathlib.Path('/opt/growing-trader/.trader-paused').is_file():raise ValueError('existing pause missing')
if not pause.exists():pause.symlink_to('/opt/growing-trader/.trader-paused')
venv=release/'venv'
if not venv.exists():subprocess.run(['/usr/bin/python3','-m','venv',str(venv)],check=True)
site=venv/'lib/python3.12/site-packages'
(site/'trading-runtime.pth').write_text(str(release/'src')+'\n/opt/growing-trader/.venv/lib/python3.12/site-packages\n')
subprocess.run([str(venv/'bin/python'),'-I','-c','import nifty_engine.agent_engine.oracle_runtime,importlib.metadata;assert importlib.metadata.version("growwapi")=="1.5.0"'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
unit=(release/'deploy/trading-observer.service.example').read_text().replace('RELEASE',sys.argv[1])
pathlib.Path('/etc/systemd/system/trading-observer.service').write_text(unit)
subprocess.run(['systemd-analyze','verify','/etc/systemd/system/trading-observer.service'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(json.dumps({'status':'READ_ONLY_RELEASE_STAGED','pause':True,'orders_enabled':False}))
'@
$taskSetup | & $taskSsh @taskSshArgs "sudo -n python3 - $Commit"
if ($LASTEXITCODE -ne 0) {throw 'Deployment staging failed'}
# Credentials go from the existing DPAPI vault straight into SSH stdin.
try { Add-Type -AssemblyName System.Security.Cryptography.ProtectedData }
catch { Add-Type -AssemblyName System.Security }
$taskVault=Join-Path (Split-Path $taskRoot -Parent) '.secrets\growing-trader'
$taskBytes=@();$taskPayload=$null
try {
    $taskKey=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $taskVault 'groww-api-key.dpapi')),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskSecret=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $taskVault 'groww-api-secret.dpapi')),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $taskBytes=@($taskKey,$taskSecret)
    $taskPayload=@{api_key=[Text.Encoding]::UTF8.GetString($taskKey).Trim([char]0xFEFF).Trim();api_secret=[Text.Encoding]::UTF8.GetString($taskSecret).Trim([char]0xFEFF).Trim()} | ConvertTo-Json -Compress
    $taskPayload | & $taskSsh @taskSshArgs "sudo -n python3 -I $taskRelease/scripts/provision_observer.py"
    if ($LASTEXITCODE -ne 0) {throw 'Credential provisioning failed'}
    # Capture mail settings only in memory and encrypt them for independent alerts.
    $taskMail=& $taskSsh @taskSshArgs "sudo -n python3 -I $taskRelease/scripts/provision_observer.py --mail-pipe"
    if ($LASTEXITCODE -ne 0) {throw 'Independent alert mail provisioning failed'}
    $taskMailBytes=[Text.Encoding]::UTF8.GetBytes(($taskMail -join "`n"))
    $taskProtected=[Security.Cryptography.ProtectedData]::Protect($taskMailBytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes((Join-Path $taskVault 'report-mail.dpapi'),$taskProtected)
    [Array]::Clear($taskMailBytes,0,$taskMailBytes.Length)
    $taskMail=$null
} finally {
    foreach($taskBuffer in $taskBytes) {if ($null -ne $taskBuffer) {[Array]::Clear($taskBuffer,0,$taskBuffer.Length)}}
    $taskPayload=$null
}
# The journal must be migrated/verified before enabling this read-only observer.
Write-Output 'Release, isolated original credentials and protected PC alerts staged. Observer not started by this script.'
