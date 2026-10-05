# Per-user Windows shortcuts and logon startup. No administrator rights or broker permissions.
[CmdletBinding()]
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskEntry = Join-Path $PSScriptRoot 'Start-TradingApp.ps1'
$taskWsh = New-Object -ComObject WScript.Shell
$taskState = Join-Path $taskRoot '.agent-state'
$taskLinks = @(
    @{path=(Join-Path ([Environment]::GetFolderPath('Desktop')) 'Options Trader.lnk'); background=$false},
    @{path=(Join-Path ([Environment]::GetFolderPath('Programs')) 'Options Trader.lnk'); background=$false},
    @{path=(Join-Path ([Environment]::GetFolderPath('Startup')) 'Options Trader Monitor.lnk'); background=$true}
)
if ($Remove) {
    foreach ($taskItem in $taskLinks) {
        if (Test-Path -LiteralPath $taskItem.path) {
            $taskLink = $taskWsh.CreateShortcut($taskItem.path)
            if ($taskLink.TargetPath -eq $taskShell -and $taskLink.Arguments.Contains($taskEntry)) {
                Remove-Item -LiteralPath $taskItem.path
            } else { throw 'An existing shortcut is not owned by this installation.' }
        }
    }
    Write-Output 'Options Trader shortcuts and logon startup removed. Local journals and credentials preserved.'
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot '.venv\Scripts\python.exe'))) { throw 'Local Python environment is missing.' }
if (-not (Test-Path -LiteralPath $taskState)) { New-Item -ItemType Directory -Path $taskState | Out-Null }
# Match the local app's three-bar mark; no downloaded asset or new runtime.
Add-Type -AssemblyName System.Drawing
$taskBitmap = New-Object Drawing.Bitmap(64,64)
$taskGraphics = [Drawing.Graphics]::FromImage($taskBitmap)
try {
    $taskGraphics.Clear([Drawing.Color]::FromArgb(242,251,247))
    foreach ($taskBar in @(@(10,40,16,'#FB861C'),@(27,25,31,'#08BB63'),@(44,10,46,'#087F3F'))) {
        $taskBrush = New-Object Drawing.SolidBrush([Drawing.ColorTranslator]::FromHtml($taskBar[3]))
        try { $taskGraphics.FillRectangle($taskBrush,[int]$taskBar[0],[int]$taskBar[1],10,[int]$taskBar[2]) } finally { $taskBrush.Dispose() }
    }
    $taskIconPath = Join-Path $taskState 'options-trader.ico'
    $taskIcon = [Drawing.Icon]::FromHandle($taskBitmap.GetHicon())
    $taskStream = [IO.File]::Create($taskIconPath)
    try { $taskIcon.Save($taskStream) } finally { $taskStream.Dispose(); $taskIcon.Dispose() }
} finally { $taskGraphics.Dispose(); $taskBitmap.Dispose() }
foreach ($taskItem in $taskLinks) {
    if (Test-Path -LiteralPath $taskItem.path) {
        $taskOld = $taskWsh.CreateShortcut($taskItem.path)
        if ($taskOld.TargetPath -ne $taskShell -or -not $taskOld.Arguments.Contains($taskEntry)) {
            throw 'Shortcut name already belongs to another application.'
        }
    }
    $taskLink = $taskWsh.CreateShortcut($taskItem.path)
    $taskLink.TargetPath = $taskShell
    $taskLink.Arguments = '-NoProfile -WindowStyle Hidden -File "' + $taskEntry + '"'
    if ($taskItem.background) { $taskLink.Arguments += ' -BackgroundOnly' }
    $taskLink.WorkingDirectory = $taskRoot
    $taskLink.WindowStyle = 7
    $taskLink.IconLocation = $taskIconPath + ',0'
    $taskLink.Description = 'Local Options Trader. Manual trades protected; execution remains gated.'
    $taskLink.Save()
}
@{format='trading-pc-install-v1'; logon_startup=$true; shortcuts=@($taskLinks | ForEach-Object { $_.path }); installed_at=[DateTimeOffset]::Now.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskState 'pc-app-install.json') -Encoding UTF8
Write-Output 'Installed Options Trader on Desktop and Start. The monitor starts automatically at Windows sign-in.'
