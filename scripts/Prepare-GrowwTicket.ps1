# Offline formatting only. Does not open Groww, prefill its forms, or submit orders.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [Parameter(Mandatory=$true)][string]$OutputFile
)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install the project in its local .venv first.' }
& $taskPython -I -m nifty_engine.agent_engine.manual_ticket --input $InputFile --output $OutputFile
exit $LASTEXITCODE
