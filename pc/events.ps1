# PC events -> Telegram, and sleep once a stream is over (paused or app closed).
# Installed to C:\ProgramData\tvwake\ by pc/install-events.ps1; settings in events.json next to it.
#
# Called by:
#   Apollo global_prep_cmd   do: start     undo: end       (app launched / app closed)
#   Apollo global_state_cmd  do: resume    undo: pause     (first client connected / all disconnected)
#   Task Scheduler           boot, wake, shutdown          (Windows events)
# While a stream runs, the start/resume worker also watches Steam's RunningAppID (registry change
# notifications, no polling) and reports each game that starts (game.start; no game.end on purpose).
#
# Apollo waits for its commands, so the hook only reads Apollo's variables and hands the work to a
# detached copy of itself (-Run). That copy is created through WMI: processes started any other way
# by a short-lived parent (Apollo hook, ssh) can be killed together with it.
param(
    [Parameter(Position = 0)][string]$Kind,  # not $Event: that is an automatic variable
    [switch]$Run,
    [string]$App = '',
    [string]$Client = '',
    [string]$Dir = 'C:\ProgramData\tvwake'   # another folder (with its own events.json) for testing
)
$ErrorActionPreference = 'Stop'
$Dir = $Dir.TrimEnd('\')  # "C:\x\" would end in \" on the worker's command line, an escaped quote
$LogFile = "$Dir\events.log"
$ApolloLog = 'C:\Program Files\Apollo\config\sunshine.log'
$SettleSeconds = 30  # after a pause/end: time to reconnect or start another app before sleeping

function Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Kind] $msg"
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Settings {
    $s = Get-Content "$Dir\events.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $s.autosleep) { $s | Add-Member autosleep 'off' }
    $s
}

# Messages look like log lines: <code>ICON key</code> details (Telegram HTML).
#   <mark> <code>ICON key</code> details, e.g.  (blue dot) [arrow stream.start] Steam Big Picture . LG TV
#   marks: blue = stream running, white = stream stopped, violet = PC power, green = ok, red = error
#   (the agent on the helper sends pc.ready / pc.error in the same style).
# This file is ASCII only: Windows PowerShell reads a .ps1 without a BOM in the ANSI code page, where
# the UTF-8 bytes of some symbols turn into quote characters and break the script. So every non-ASCII
# character is built from its code point.
function U([int[]]$cp) { -join ($cp | ForEach-Object { [char]::ConvertFromUtf32($_) }) }
$Icon = @{
    start = U 0x2197; resume = U 0x21BB; pause = U 0x2016; end = U 0x25A0          # arrow NE, cycle, double bar, square
    sleep = U 0x2193; wake = U 0x2191; on = U 0x23FB; off = U 0x23CF, 0xFE0E      # down, up, power, eject (as text)
    ok = U 0x2713; game = U 0x25B8                                                 # check mark, small triangle
}
$Dot = " $(U 0xB7) "  # " . " middle dot between details
$Live = U 0x1F535; $Idle = U 0x26AA, 0xFE0F; $Power = U 0x1F7E3; $Good = U 0x1F7E2   # blue, white, violet, green
$Mark = @{
    'stream.start' = $Live; 'stream.resume' = $Live; 'stream.pause' = $Idle; 'stream.end' = $Idle; 'game.start' = $Live
    'pc.sleep' = $Power; 'pc.wake' = $Power; 'pc.on' = $Power; 'pc.off' = $Power; 'events.ok' = $Good
}
function Html([string]$x) { $x.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;') }
function Send([string]$icon, [string]$key, [string]$detail) {
    $text = "$($Mark[$key]) <code>$icon $key</code> $(Html $detail)".Trim()
    $s = Settings
    if (-not $s.telegram_bot_token -or -not $s.telegram_chat_id) { Log "no telegram settings: $key $detail"; return }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $body = @{ chat_id = $s.telegram_chat_id; text = $text; parse_mode = 'HTML'; disable_web_page_preview = $true } | ConvertTo-Json -Compress
    for ($i = 1; $i -le 3; $i++) {
        try {
            Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$($s.telegram_bot_token)/sendMessage" `
                -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 10 | Out-Null
            Log "sent: $key $detail"; return
        } catch {
            Log "send attempt $i failed: $($_.Exception.Message)"   # the message never contains the URL
            Start-Sleep -Seconds (5 * $i)                            # e.g. network not up yet after wake
        }
    }
}

function Stream-Starts {
    # How often a stream has started or resumed in Apollo's current log. If it grows while we wait,
    # someone connected again; if it shrinks, Apollo restarted (new log). Either way: don't sleep.
    @(Select-String -Path $ApolloLog -Pattern 'New streaming session|Session resuming').Count
}

function Mark([string]$name) { Set-Content "$Dir\last-$name" (Get-Date).ToString('o') }
# Values pass through the command line as base64 of "#" + value, so an empty value is never an empty
# argument (PowerShell -File refuses "-Client" without a value).
function Enc([string]$x) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("#$x")) }
function Dec([string]$x) { if (-not $x) { return '' }; [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($x)).Substring(1) }
function Seconds-Since([string]$name) {
    try { ((Get-Date) - [datetime](Get-Content "$Dir\last-$name" -ErrorAction Stop)).TotalSeconds } catch { 1e9 }
}
function Duration([double]$sec) {
    if ($sec -ge 1e8) { return '' }
    $m = [int][math]::Round($sec / 60)
    if ($m -lt 60) { return "$m min" }
    "$([math]::Floor($m / 60)) h $($m % 60) min"
}

function Steam-Game([int]$id) {
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\Valve\Steam\Apps\$id")
    $name = if ($k) { $k.GetValue('Name') } else { $null }
    if ($name) { $name } else { "Steam app $id" }
}

function Watch-Games {
    # One watcher at a time (start and resume both call this). Wait up to 70 s: a watcher from a stream
    # that just ended notices the end within 60 s and lets go; one of the same stream never does.
    $mutex = New-Object Threading.Mutex($false, 'Global\TvWakeGameWatcher')
    try { if (-not $mutex.WaitOne(70000)) { Log 'game watcher already running'; return } }
    catch [Threading.AbandonedMutexException] { }  # the previous watcher died: we own it now
    try {
        $steam = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Valve\Steam')
        if (-not $steam) { Log 'no Steam registry key: game watcher off'; return }
        Add-Type -Namespace TvWake -Name Reg -MemberDefinition @'
[DllImport("advapi32.dll")] public static extern int RegNotifyChangeKeyValue(System.IntPtr key, bool subtree, int filter, System.IntPtr evt, bool async);
'@
        $began = Get-Date
        $last = [int]$steam.GetValue('RunningAppID', 0)  # whatever runs now (or a stale value) is not news
        $evt = New-Object Threading.AutoResetEvent $false
        Log "game watcher on (RunningAppID=$last)"
        while ($true) {
            # REG_NOTIFY_CHANGE_LAST_SET (4): fires when a value of the Steam key is written. Re-armed each time.
            $rc = [TvWake.Reg]::RegNotifyChangeKeyValue($steam.Handle.DangerousGetHandle(), $false, 4, $evt.SafeWaitHandle.DangerousGetHandle(), $true)
            if ($rc -ne 0) { Log "RegNotifyChangeKeyValue failed ($rc): game watcher off"; return }
            [void]$evt.WaitOne(60000)  # a change, or once a minute to see whether the stream is over
            $alive = ((Get-Date) - $began).TotalSeconds
            if ((Seconds-Since 'end') -lt $alive) { Log 'stream ended: game watcher off'; return }
            if ($alive -gt 48 * 3600) { Log 'game watcher: 48 h, stopping'; return }
            $id = [int]$steam.GetValue('RunningAppID', 0)
            if ($id -ne $last) {
                if ($id -ne 0) { Send $Icon.game 'game.start' (Steam-Game $id) }
                $last = $id
            }
        }
    } finally {
        try { $mutex.ReleaseMutex() } catch {}
        $mutex.Dispose()
    }
}

function Sleep-When-Idle([string]$why) {
    $s = Settings
    if ($s.autosleep -eq 'off') { return }
    $before = Stream-Starts
    Start-Sleep -Seconds ([double]$s.autosleep_after_s + $SettleSeconds)
    $after = Stream-Starts
    if ($after -ne $before) { Log "stream started again ($before -> $after): not sleeping"; return }
    if ($s.autosleep -ne 'on') { Send $Icon.sleep 'pc.sleep' "dry run, would sleep: $why"; return }
    Send $Icon.sleep 'pc.sleep' $why
    Log 'sleeping'
    & rundll32.exe powrprof.dll,SetSuspendState 0,1,0   # hibernation is off here, so this is S3
}

# ---- hook mode: hand over to a detached copy and return to Apollo at once -----------------------
# Must never fail: if a prep command fails, Apollo does not start the app.
if (-not $Run) {
    try {
        if ($Kind -eq 'start') { Mark 'start' }  # here, not in the worker: the resume hook follows at once
        if ($Kind -eq 'end') { Mark 'end' }      # tells the game watcher to stop
        $a = if ($env:APOLLO_APP_NAME) { $env:APOLLO_APP_NAME } else { $env:SUNSHINE_APP_NAME }
        $c = if ($env:APOLLO_CLIENT_NAME) { $env:APOLLO_CLIENT_NAME } else { $env:SUNSHINE_CLIENT_NAME }
        $cmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\events.ps1`" $Kind -Run -App $(Enc $a) -Client $(Enc $c) -Dir `"$Dir`""
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
        if ($r.ReturnValue -ne 0) { Log "could not start worker ($($r.ReturnValue))" }
    } catch {
        Log "hook error: $($_.Exception.Message)"
    }
    exit 0
}

# ---- worker mode ----------------------------------------------------------------------------------
$App = Dec $App
$Client = Dec $Client
$on = (@($App, $Client) | Where-Object { $_ }) -join $Dot   # "Steam Big Picture . LG TV"
try {
    switch ($Kind) {
        'start'  { Send $Icon.start 'stream.start' $on; Watch-Games }
        'resume' { if ((Seconds-Since 'start') -gt 20) { Send $Icon.resume 'stream.resume' $on }; Watch-Games }  # skip the resume that comes with a start
        'pause'  { Send $Icon.pause 'stream.pause' ((@('client disconnected', $App) | Where-Object { $_ }) -join $Dot); Sleep-When-Idle 'stream paused' }
        'end'    { Send $Icon.end 'stream.end' ((@($App, (Duration (Seconds-Since 'start'))) | Where-Object { $_ }) -join $Dot); Sleep-When-Idle 'stream ended' }
        'boot'   { Send $Icon.on 'pc.on' 'cold boot' }
        'wake'   {
            # Kernel-Power 42 ("entering sleep") has the right time; 107 ("resumed") does not.
            $slept = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 42 } -MaxEvents 1 -ErrorAction SilentlyContinue
            Send $Icon.wake 'pc.wake' $(if ($slept) { "asleep since $($slept.TimeCreated.ToString('HH:mm'))" } else { '' })
        }
        'shutdown' { Send $Icon.off 'pc.off' 'shutdown or restart' }
        'test'     { $s = Settings; Send $Icon.ok 'events.ok' "auto-sleep: $($s.autosleep)" }
        default  { Log "unknown event" }
    }
} catch {
    Log "error: $($_.Exception.Message)"
}
