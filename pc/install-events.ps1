# Install pc/events.ps1: Telegram messages for stream and power events, and self-sleep once a stream
# is over (paused or app closed). Run elevated (an admin ssh session counts):
#
#   powershell -ExecutionPolicy Bypass -File pc\install-events.ps1            # settings from ..\.env
#   powershell -ExecutionPolicy Bypass -File pc\install-events.ps1 -Uninstall
#
# What it changes (all reversible with -Uninstall):
#   C:\ProgramData\tvwake\       events.ps1, events.json (settings), events.log
#   Apollo sunshine.conf         global_prep_cmd (start/end) and global_state_cmd (resume/pause);
#                                backup next to it; ApolloService is restarted
#   Task Scheduler \TvWake\      Boot (at startup), Wake (Power-Troubleshooter 1), Shutdown (User32 1074)
# It also removes the old helper ssh key (tvwake-autosleep) if it is still there.
param([string]$EnvFile = (Join-Path $PSScriptRoot '..\.env'), [switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$Dir = 'C:\ProgramData\tvwake'
$Conf = 'C:\Program Files\Apollo\config\sunshine.conf'
$TaskPath = '\TvWake\'

function Set-ApolloHooks([string[]]$newLines) {
    Copy-Item $Conf "$Conf.bak-events" -Force
    $keep = @(Get-Content $Conf | Where-Object { $_ -notmatch '^\s*(global_prep_cmd|global_state_cmd)\s*=' })
    [IO.File]::WriteAllText($Conf, (($keep + $newLines) -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
    Restart-Service ApolloService
}

# The old polling setup (helper ssh key + script) is replaced by this.
$auth = 'C:\ProgramData\ssh\administrators_authorized_keys'
if ((Test-Path $auth) -and (Select-String -Path $auth -Pattern 'tvwake-autosleep' -Quiet)) {
    $lines = @(Get-Content $auth | Where-Object { $_ -notmatch 'tvwake-autosleep' })
    [IO.File]::WriteAllText($auth, ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding $false))
    Write-Host 'Removed the old tvwake-autosleep ssh key.'
}
Remove-Item "$Dir\autosleep.ps1" -ErrorAction SilentlyContinue

if ($Uninstall) {
    Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    Set-ApolloHooks @()
    Remove-Item "$Dir\events.ps1", "$Dir\events.json" -ErrorAction SilentlyContinue
    Write-Host 'Uninstalled (Apollo hooks and scheduled tasks removed).'
    exit 0
}

# ---- settings from .env -------------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) { throw "No .env at $EnvFile (pass -EnvFile)." }
$envs = @{}
foreach ($l in Get-Content $EnvFile -Encoding UTF8) {
    if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $v = $Matches[2]; if ($v.Length -ge 2 -and $v[0] -eq $v[-1] -and "'`"".Contains($v[0])) { $v = $v.Substring(1, $v.Length - 2) }
        $envs[$Matches[1]] = $v
    }
}
$mode = if ($envs.AUTOSLEEP) { $envs.AUTOSLEEP.ToLower() } else { 'off' }
if ($mode -notin 'off', 'dry-run', 'on') { throw "AUTOSLEEP must be off, dry-run or on (got $mode)" }
$settings = [ordered]@{
    telegram_bot_token = "$($envs.TELEGRAM_BOT_TOKEN)"
    telegram_chat_id   = "$($envs.TELEGRAM_CHAT_ID)"
    autosleep          = $mode
    autosleep_after_s  = [double]("0$($envs.AUTOSLEEP_AFTER_MIN)") * 60
    autosleep_game_s   = $(if ($envs.AUTOSLEEP_GAME_MIN) { [double]$envs.AUTOSLEEP_GAME_MIN * 60 } else { 900 })
}

# ---- files ----------------------------------------------------------------------------------------
New-Item -ItemType Directory -Force $Dir | Out-Null
# Admins/SYSTEM full; users may write (Apollo runs the hooks as the logged-in user: log + timestamps).
icacls $Dir /inheritance:r /grant 'Administrators:(OI)(CI)F' /grant 'SYSTEM:(OI)(CI)F' /grant 'Users:(OI)(CI)M' | Out-Null
# With a BOM: Windows PowerShell reads a .ps1 without one in the ANSI code page (breaks the emoji/Cyrillic).
$code = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'events.ps1'), [Text.Encoding]::UTF8)
[IO.File]::WriteAllText("$Dir\events.ps1", $code, (New-Object Text.UTF8Encoding $true))
[IO.File]::WriteAllText("$Dir\events.json", ($settings | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
# Users must not be able to change the code that SYSTEM runs from the scheduled tasks.
foreach ($f in 'events.ps1', 'events.json') {
    icacls "$Dir\$f" /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' /grant 'Users:R' | Out-Null
}

# ---- Apollo hooks -----------------------------------------------------------------------------------
function Hook([string]$e) { "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\events.ps1`" $e" }
$prep = ConvertTo-Json -Compress @(@{ do = (Hook 'start'); undo = (Hook 'end'); elevated = $false })
$state = ConvertTo-Json -Compress @(@{ do = (Hook 'resume'); undo = (Hook 'pause'); elevated = $false })
Set-ApolloHooks @("global_prep_cmd = $prep", "global_state_cmd = $state")

# ---- scheduled tasks for power events ---------------------------------------------------------------
function Task-Action([string]$e) {
    New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\events.ps1`" $e -Run"
}
function Event-Trigger([string]$provider, [int]$id) {
    $cls = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
    $t = New-CimInstance -CimClass $cls -ClientOnly
    $t.Enabled = $true
    $t.Subscription = "<QueryList><Query Id='0' Path='System'><Select Path='System'>*[System[Provider[@Name='$provider'] and EventID=$id]]</Select></Query></QueryList>"
    $t
}
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$opts = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$boot = New-ScheduledTaskTrigger -AtStartup; $boot.Delay = 'PT30S'   # give the network a moment
Register-ScheduledTask -TaskPath $TaskPath -TaskName 'Boot' -Action (Task-Action 'boot') -Trigger $boot -Principal $principal -Settings $opts -Force | Out-Null
Register-ScheduledTask -TaskPath $TaskPath -TaskName 'Wake' -Action (Task-Action 'wake') -Trigger (Event-Trigger 'Microsoft-Windows-Power-Troubleshooter' 1) -Principal $principal -Settings $opts -Force | Out-Null
Register-ScheduledTask -TaskPath $TaskPath -TaskName 'Shutdown' -Action (Task-Action 'shutdown') -Trigger (Event-Trigger 'User32' 1074) -Principal $principal -Settings $opts -Force | Out-Null

Write-Host "Installed. AUTOSLEEP=$mode, telegram=$([bool]$settings.telegram_bot_token)"
Write-Host "Log: $Dir\events.log"
# Same path as an Apollo hook (hook mode -> detached worker via WMI), so the test message proves all of it.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Dir\events.ps1" test
Write-Host 'A test message should arrive in Telegram within a few seconds.'
