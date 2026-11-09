# PC events -> Telegram, and sleep once a stream is over (paused or app closed).
# Installed to C:\ProgramData\tvwake\ by pc/install-events.ps1; settings in events.json next to it.
#
# Called by:
#   Apollo global_prep_cmd   do: start     undo: end       (app launched / app closed)
#   Apollo global_state_cmd  do: resume    undo: pause     (first client connected / all disconnected)
#   Task Scheduler           boot, wake, shutdown, apollo  (Windows events; apollo = service crashed)
#                            watch                         (at logon, as the user, elevated: countdown window with
#                                                           Cancel / Esc x3 while a sleep/shutdown is pending, see Watch-Plan)
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
    [string]$Mode = '',
    [string]$Dir = 'C:\ProgramData\tvwake'   # another folder (with its own events.json) for testing
)
$ErrorActionPreference = 'Stop'
$Dir = $Dir.TrimEnd('\')  # "C:\x\" would end in \" on the worker's command line, an escaped quote
$Testing = $Dir -ne 'C:\ProgramData\tvwake'  # messages from a test folder say "(test)"
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
    if ($null -eq $s.autosleep_game_s) { $s | Add-Member autosleep_game_s 900 }  # older events.json
    $s
}

# Messages look like log lines: <code>ICON key</code> details (Telegram HTML).
#   <mark> <code>ICON key</code> details, e.g.  (blue dot) [arrow stream.start] Steam . LG TV
#   marks: blue = stream running, white = stream stopped, violet = PC power, green = ok, red = error
#   (the agent on the helper sends pc.ready / pc.error in the same style).
# This file is ASCII only: Windows PowerShell reads a .ps1 without a BOM in the ANSI code page, where
# the UTF-8 bytes of some symbols turn into quote characters and break the script. So every non-ASCII
# character is built from its code point.
function U([int[]]$cp) { -join ($cp | ForEach-Object { [char]::ConvertFromUtf32($_) }) }
$Icon = @{
    start = U 0x2197; resume = U 0x21BB; pause = U 0x2016; end = U 0x25A0          # arrow NE, cycle, double bar, square
    sleep = U 0x2193; wake = U 0x2191; on = U 0x23FB; off = U 0x23CF, 0xFE0E      # down, up, power, eject (as text)
    ok = U 0x2713; game = U 0x25B8; err = U 0x2715                                 # check mark, small triangle, cross
}
$Dot = " $(U 0xB7) "  # " . " middle dot between details
$Live = U 0x1F535; $Idle = U 0x26AA, 0xFE0F; $Power = U 0x1F7E3; $Good = U 0x1F7E2; $Bad = U 0x1F534   # blue, white, violet, green, red
$Mark = @{
    'stream.start' = $Live; 'stream.resume' = $Live; 'stream.pause' = $Idle; 'stream.end' = $Idle; 'game.start' = $Live
    'pc.sleep' = $Power; 'pc.wake' = $Power; 'pc.on' = $Power; 'pc.off' = $Power; 'events.ok' = $Good
    'pc.crash' = $Bad; 'apollo.down' = $Bad; 'pc.stay' = $Good
}
function Html([string]$x) { $x.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;') }
function Send([string]$icon, [string]$key, [string]$detail) {
    if ($Testing) { $detail = "$detail (test)".Trim() }
    $text = "$($Mark[$key]) <code>$icon $key</code> $(Html $detail)".Trim()
    $s = Settings
    if (-not $s.telegram_bot_token -or -not $s.telegram_chat_id) { Log "no telegram settings: $key $detail"; return }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    [Net.WebRequest]::DefaultWebProxy = $null  # skip proxy auto-detection (WPAD): seconds per new process
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
    if ($m -lt 1) { return '' }       # no "0 min"
    if ($m -lt 60) { return "$m min" }
    "$([math]::Floor($m / 60)) h $($m % 60) min"
}

# Named events between the hooks and the waiting workers (no timers, no polling):
#   StreamEnded: set by the end hook; stops the game watcher.
#   StreamBack:  set by the start/resume hooks; cancels a pending sleep or shutdown at once.
#   Plan:        set while a sleep/shutdown is pending; wakes the countdown window.
#   LocalUser:   set when someone presses Cancel in that window; cancels the pending plan.
# Created with an explicit DACL so processes of any integrity level (Apollo hooks, WMI workers, the
# logon task) can all open them.
function Named-Event([string]$name) {
    $sec = New-Object Security.AccessControl.EventWaitHandleSecurity
    foreach ($sid in 'S-1-5-11', 'S-1-5-18') {  # Authenticated Users, SYSTEM
        $who = New-Object Security.Principal.SecurityIdentifier $sid
        $sec.AddAccessRule((New-Object Security.AccessControl.EventWaitHandleAccessRule($who, 'FullControl', 'Allow')))
    }
    $created = $false
    New-Object Threading.EventWaitHandle($false, [Threading.EventResetMode]::ManualReset, "Global\TvWake$name", [ref]$created, $sec)
}
function End-Event { Named-Event 'StreamEnded' }
function Signal([string]$name) { $e = Named-Event $name; [void]$e.Set(); $e.Dispose() }

# A pending sleep/shutdown, so the next start/resume message can say it was cancelled.
function Save-Plan([string]$kind, [double]$wait, [string]$why) {
    $at = (Get-Date).AddSeconds($wait)
    Set-Content "$Dir\plan" (@{ kind = $kind; why = $why; at = $at.ToString('o'); until = $at.AddSeconds(60).ToString('o') } | ConvertTo-Json -Compress)
}
function Take-Plan {
    try { $p = Get-Content "$Dir\plan" -Raw -ErrorAction Stop | ConvertFrom-Json } catch { return '' }
    Remove-Item "$Dir\plan" -ErrorAction SilentlyContinue
    if ((Get-Date) -lt [datetime]$p.until) { "$($p.kind) cancelled" } else { '' }
}

# Games started during the current stream (for the summary in stream.end).
function Add-Played([string]$name) { Add-Content "$Dir\played" $name -Encoding UTF8 }
function Played-List {
    try { $g = @(Get-Content "$Dir\played" -Encoding UTF8 -ErrorAction Stop | Where-Object { $_ } | Select-Object -Unique) } catch { return '' }
    if ($g.Count) { "played $($g -join ', ')" } else { '' }
}

# Codec and encoder errors of the current stream, from Apollo's log (lines after the start mark).
function Session-Stats {
    try { $since = [datetime](Get-Content "$Dir\last-start" -ErrorAction Stop) } catch { return @() }
    $codec = ''; $errors = 0
    foreach ($m in Select-String -Path $ApolloLog -Pattern '^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\]: \w+: (Creating encoder \[(\w+?)_|Could not encode video packet)') {
        $t = [datetime]::ParseExact($m.Matches[0].Groups[1].Value, 'yyyy-MM-dd HH:mm:ss', $null)
        if ($t -lt $since) { continue }
        $c = $m.Matches[0].Groups[3].Value
        if ($c) { $codec = @{ hevc = 'HEVC'; h264 = 'H.264'; av1 = 'AV1' }[$c] } else { $errors++ }
    }
    @($codec, $(if ($errors) { "$errors encoder error$(if ($errors -ne 1) { 's' })" }))
}

# The mode the client asked for, from Apollo's variables (hook mode only).
function Client-Mode {
    $v = { param($n) $x = [Environment]::GetEnvironmentVariable("APOLLO_CLIENT_$n"); if (-not $x) { $x = [Environment]::GetEnvironmentVariable("SUNSHINE_CLIENT_$n") }; $x }
    $w = & $v 'WIDTH'; $h = & $v 'HEIGHT'; $fps = & $v 'FPS'; $hdr = & $v 'HDR'
    if (-not $w -or -not $h) { return '' }
    $res = @{ '3840x2160' = '4K'; '2560x1440' = '1440p'; '1920x1080' = '1080p'; '1280x720' = '720p' }["${w}x${h}"]
    if (-not $res) { $res = "${w}x${h}" }
    $out = $res
    if ($fps) { $out += " $([math]::Round([double]::Parse($fps, [Globalization.CultureInfo]::InvariantCulture))) Hz" }
    if ($hdr -eq 'true') { $out += ' HDR' }
    $out
}

function Steam-Game([int]$id) {
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\Valve\Steam\Apps\$id")
    $name = if ($k) { $k.GetValue('Name') } else { $null }
    if ($name) { $name } else { "Steam app $id" }
}

function Watch-Games {
    # One watcher at a time (start and resume both call this). A watcher of a stream that just ended
    # lets go at once (end event); wait a little in case it is still finishing.
    $mutex = New-Object Threading.Mutex($false, 'Global\TvWakeGameWatcher')
    try { if (-not $mutex.WaitOne(15000)) { Log 'game watcher already running'; return } }
    catch [Threading.AbandonedMutexException] { }  # the previous watcher died: we own it now
    try {
        $steam = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Valve\Steam')
        if (-not $steam) { Log 'no Steam registry key: game watcher off'; return }
        Add-Type -Namespace TvWake -Name Reg -MemberDefinition @'
[DllImport("advapi32.dll")] public static extern int RegNotifyChangeKeyValue(System.IntPtr key, bool subtree, int filter, System.IntPtr evt, bool async);
'@
        $ended = End-Event
        [void]$ended.Reset()  # an end from an earlier stream does not count...
        # ...but this stream may already be over (e.g. the start message took a while to send).
        if ((Seconds-Since 'end') -lt (Seconds-Since 'start')) { Log 'stream already ended: no game watcher'; return }
        $last = [int]$steam.GetValue('RunningAppID', 0)  # whatever runs now (or a stale value) is not news
        $changed = New-Object Threading.AutoResetEvent $false
        Log "game watcher on (RunningAppID=$last)"
        while ($true) {
            # REG_NOTIFY_CHANGE_LAST_SET (4): fires when a value of the Steam key is written. Re-armed each time.
            $rc = [TvWake.Reg]::RegNotifyChangeKeyValue($steam.Handle.DangerousGetHandle(), $false, 4, $changed.SafeWaitHandle.DangerousGetHandle(), $true)
            if ($rc -ne 0) { Log "RegNotifyChangeKeyValue failed ($rc): game watcher off"; return }
            # Sleeps until Steam writes its key or the stream ends. No timer, no polling.
            if ([Threading.WaitHandle]::WaitAny([Threading.WaitHandle[]]@($changed, $ended)) -eq 1) { Log 'stream ended: game watcher off'; return }
            $id = [int]$steam.GetValue('RunningAppID', 0)
            if ($id -ne $last) {
                if ($id -ne 0) { $g = Steam-Game $id; Add-Played $g; Send $Icon.game 'game.start' $g }
                $last = $id
            }
        }
    } finally {
        try { $mutex.ReleaseMutex() } catch {}
        $mutex.Dispose()
    }
}

function Running-Game {
    # Steam's RunningAppID, or 0. Steam can leave a stale value after it quit, so only while it runs.
    if (-not (Get-Process steam -ErrorAction SilentlyContinue)) { return 0 }
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Valve\Steam')
    if ($k) { [int]$k.GetValue('RunningAppID', 0) } else { 0 }
}

function Idle-Plan {
    # What happens once a stream stopped, decided up front so the pause/end message can say it.
    # Nothing right after someone pressed Cancel at the PC.
    # Many games do not survive S3 (the GPU is powered off; they hang or show black after waking), and
    # not every game can be closed from here (elevated or anti-cheat protected processes, non-Steam
    # games). So: no game -> sleep after 30 s. A game left running -> give it AUTOSLEEP_GAME_MIN for a
    # break, then shut the PC down (forced): the next start is a cold boot (~1 min) but always clean.
    $s = Settings
    if ($s.autosleep -eq 'off') { return $null }
    if ((Seconds-Since 'localuser') -lt 120) { return @{ wait = 0; kind = ''; text = 'someone is at the PC, staying on'; none = $true } }
    $wait = [double]$s.autosleep_after_s + $SettleSeconds
    $game = Running-Game
    $at = { param($sec) (Get-Date).AddSeconds($sec).ToString('HH:mm') }
    $kind = 'sleep'
    if ($game) {
        $wait = [math]::Max($wait, [double]$s.autosleep_game_s)
        $kind = 'shutdown'
        $text = "$(Steam-Game $game) still running$($Dot)shutdown at $(& $at $wait)"
    } elseif ($wait -lt 120) {
        $text = "sleep in $([int]$wait) s"
    } else {
        $text = "sleep at $(& $at $wait)"
    }
    if ($s.autosleep -ne 'on') { $text += ' (test mode)' }
    @{ wait = $wait; text = $text; kind = $kind }
}

function Sleep-When-Idle([string]$why, $plan) {
    if (-not $plan -or $plan.none) { return }  # auto-sleep off, or someone is at the PC
    $s = Settings
    Log "plan: $($plan.text)"
    Save-Plan $plan.kind $plan.wait $plan.text
    $back = Named-Event 'StreamBack'; $local = Named-Event 'LocalUser'
    [void]$back.Reset(); [void]$local.Reset()  # a reconnect before this point is still caught by the count below
    $pending = Named-Event 'Plan'; [void]$pending.Set()  # wake the countdown window
    $before = Stream-Starts
    $hit = [Threading.WaitHandle]::WaitAny([Threading.WaitHandle[]]@($back, $local), [int]($plan.wait * 1000))
    if ($hit -eq 0) { Log 'stream is back: plan cancelled'; return }  # the start/resume message says so
    if ($hit -eq 1) { Log 'cancelled at the PC'; return }  # the countdown window said so
    $after = Stream-Starts
    if ($after -ne $before) { Log "stream started again ($before -> $after): not sleeping"; return }
    Remove-Item "$Dir\plan" -ErrorAction SilentlyContinue
    $game = Running-Game
    $test = $s.autosleep -ne 'on'
    if ($game) {
        $name = Steam-Game $game
        if ($test) { Send $Icon.off 'pc.off' "$name left running$($Dot)test mode, would shut down"; return }
        Send $Icon.off 'pc.off' "$name left running, shutting down"
        Mark 'selfoff'  # the Shutdown task stays quiet: this message already said it
        Log 'shutting down'
        & shutdown.exe /s /f /t 0 /c "tv-wake: $name was left running after the stream"
        return
    }
    if ($test) { Send $Icon.sleep 'pc.sleep' "$why$($Dot)test mode, staying on"; return }
    Send $Icon.sleep 'pc.sleep' "$why, going to sleep"
    Log 'sleeping'
    & rundll32.exe powrprof.dll,SetSuspendState 0,1,0   # hibernation is off here, so this is S3
}

function Watch-Plan {
    # Runs at logon as the signed-in user, elevated (task \TvWake\Watch). Sleeps on the Plan event; while a
    # plan is pending it shows a small always-on-top countdown bottom-right with a Cancel button, and also
    # takes Esc pressed 3 times within 2 s (works with the monitor dark during a paused stream, when the
    # window can't be seen). Only these deliberate actions cancel: a bumped mouse must not end a game.
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    Add-Type -Namespace TvWake -Name Keys -MemberDefinition '[DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vKey);'
    $plan = Named-Event 'Plan'
    Log 'countdown watcher on'
    while ($true) {
        [void]$plan.WaitOne()
        try { $p = Get-Content "$Dir\plan" -Raw | ConvertFrom-Json } catch { $p = $null }
        if ($p) { Show-Countdown $p }
        [void]$plan.Reset()
    }
}

function Show-Countdown($p) {
    $at = [datetime]$p.at
    $f = New-Object Windows.Forms.Form
    $f.FormBorderStyle = 'None'; $f.TopMost = $true; $f.ShowInTaskbar = $false; $f.StartPosition = 'Manual'
    $f.BackColor = [Drawing.Color]::FromArgb(24, 28, 36); $f.ForeColor = [Drawing.Color]::White
    $f.Size = New-Object Drawing.Size(340, 108); $f.Opacity = 0.94
    $wa = [Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $f.Location = New-Object Drawing.Point(($wa.Right - $f.Width - 16), ($wa.Bottom - $f.Height - 16))
    $big = New-Object Windows.Forms.Label
    $big.Font = New-Object Drawing.Font('Consolas', 28, [Drawing.FontStyle]::Bold); $big.AutoSize = $true
    $big.Location = New-Object Drawing.Point(14, 8)
    $small = New-Object Windows.Forms.Label
    $small.Font = New-Object Drawing.Font('Segoe UI', 9); $small.AutoSize = $false; $small.Size = New-Object Drawing.Size(200, 40)
    $small.Location = New-Object Drawing.Point(16, 60); $small.ForeColor = [Drawing.Color]::FromArgb(170, 180, 195)
    $small.Text = "$(if ($p.kind -eq 'shutdown') { 'PC shuts down' } else { 'PC goes to sleep' })`n$(($p.why -replace "$([regex]::Escape($Dot))", ', '))"
    $btn = New-Object Windows.Forms.Button
    $btn.Text = 'Cancel'; $btn.Size = New-Object Drawing.Size(100, 40); $btn.Location = New-Object Drawing.Point(226, 52)
    $btn.FlatStyle = 'Flat'; $btn.BackColor = [Drawing.Color]::FromArgb(60, 66, 80); $btn.ForeColor = [Drawing.Color]::White
    $cancel = {
        param([string]$how)
        Mark 'localuser'
        Remove-Item "$Dir\plan" -ErrorAction SilentlyContinue
        Signal 'LocalUser'
        # A paused stream keeps the monitor off (only Apollo's virtual display is on): end the session so
        # the monitor comes back. The game keeps running on the monitor; the TV can't resume it any more.
        $paused = (Select-String -Path $ApolloLog -Pattern 'New streaming session|Session resuming|Session pausing' | Select-Object -Last 1).Line -match 'Session pausing'
        Send $Icon.ok 'pc.stay' (J "$($p.kind) cancelled at the PC ($how)" $(if ($paused) { 'stream closed, monitor on' }))
        $f.Close()
        if ($paused) { Log 'restarting Apollo to end the paused stream'; Restart-Service ApolloService -Force }
    }
    $btn.Add_Click({ & $cancel 'Cancel' })
    $f.Controls.AddRange(@($big, $small, $btn))
    $tick = New-Object Windows.Forms.Timer; $tick.Interval = 500
    $update = {
        $left = [int][math]::Ceiling(($at - (Get-Date)).TotalSeconds)
        if (-not (Test-Path "$Dir\plan") -or $left -lt -5) { $f.Close(); return }  # done, or cancelled elsewhere
        $left = [math]::Max(0, $left)
        $big.Text = '{0}:{1:00}' -f [math]::Floor($left / 60), ($left % 60)
    }
    $tick.Add_Tick($update); & $update; $tick.Start()
    # Esc x3 within 2 s, checked every 50 ms (key state is global: works whatever window has the focus).
    $esc = @{ down = $true; times = @() }  # "down" at start: a key held from before does not count
    $keys = New-Object Windows.Forms.Timer; $keys.Interval = 50
    $keys.Add_Tick({
        $down = ([TvWake.Keys]::GetAsyncKeyState(0x1B) -band 0x8000) -ne 0
        if ($down -and -not $esc.down) {
            $now = Get-Date
            $esc.times = @($esc.times | Where-Object { ($now - $_).TotalSeconds -lt 2 }) + $now
            if ($esc.times.Count -ge 3) { $keys.Stop(); & $cancel 'Esc x3' }
        }
        $esc.down = $down
    })
    $keys.Start()
    [void]$f.ShowDialog()
    $tick.Stop(); $tick.Dispose(); $keys.Stop(); $keys.Dispose(); $f.Dispose()
}

# ---- hook mode: hand over to a detached copy and return to Apollo at once -----------------------
# Must never fail: if a prep command fails, Apollo does not start the app.
if (-not $Run) {
    try {
        # Done here, not in the worker, so they happen in order (the resume hook follows the start at once).
        if ($Kind -eq 'start') { Mark 'start'; Remove-Item "$Dir\played" -ErrorAction SilentlyContinue }
        if ($Kind -in 'start', 'resume') { Signal 'StreamBack' }  # cancels a pending sleep/shutdown
        if ($Kind -eq 'end') { Mark 'end'; Signal 'StreamEnded' }  # stops the game watcher
        $a = if ($env:APOLLO_APP_NAME) { $env:APOLLO_APP_NAME } else { $env:SUNSHINE_APP_NAME }
        $c = if ($env:APOLLO_CLIENT_NAME) { $env:APOLLO_CLIENT_NAME } else { $env:SUNSHINE_CLIENT_NAME }
        $cmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\events.ps1`" $Kind -Run -App $(Enc $a) -Client $(Enc $c) -Mode $(Enc (Client-Mode)) -Dir `"$Dir`""
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
        if ($r.ReturnValue -ne 0) { Log "could not start worker ($($r.ReturnValue))" }
    } catch {
        Log "hook error: $($_.Exception.Message)"
    }
    exit 0
}

# ---- worker mode ----------------------------------------------------------------------------------
$App = (Dec $App) -replace '\s*Big Picture\s*$', ''   # "Steam Big Picture" -> "Steam"
$Client = Dec $Client
$Mode = Dec $Mode
function J { (@($args) | ForEach-Object { $_ } | Where-Object { $_ }) -join $Dot }  # join the non-empty parts
$on = J $App $Client   # "Steam . LG TV"
try {
    switch ($Kind) {
        'start'  { Send $Icon.start 'stream.start' (J $on $Mode (Take-Plan)); Watch-Games }
        'resume' {
            if ((Seconds-Since 'start') -gt 20) { Send $Icon.resume 'stream.resume' (J $on $Mode (Take-Plan)) }  # skip the resume that comes with a start
            Watch-Games
        }
        'pause'  {
            $plan = Idle-Plan
            Send $Icon.pause 'stream.pause' (J 'client disconnected' $App $plan.text)
            Sleep-When-Idle 'stream paused' $plan
        }
        'end'    {
            $plan = Idle-Plan
            Send $Icon.end 'stream.end' (J $App (Duration (Seconds-Since 'start')) (Played-List) (Session-Stats) $plan.text)
            Sleep-When-Idle 'stream ended' $plan
        }
        'boot'   {
            $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
            # 6008 / Kernel-Power 41 are logged early in a boot that followed a crash or power loss.
            $crash = Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 6008; StartTime = $boot } -MaxEvents 1 -ErrorAction SilentlyContinue
            if (-not $crash) { $crash = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = $boot } -MaxEvents 1 -ErrorAction SilentlyContinue }
            if ($crash) { Send $Icon.err 'pc.crash' 'the last shutdown was unexpected (power loss, crash or reset button)' }
            # Signed in = the user's explorer.exe started (auto-login). Give it up to 2 minutes.
            $shell = $null
            for ($i = 0; $i -lt 24 -and -not $shell; $i++) {
                $shell = Get-Process explorer -IncludeUserName -ErrorAction SilentlyContinue | Where-Object { $_.UserName -and $_.UserName -notmatch 'SYSTEM|DWM|UMFD' } | Sort-Object StartTime | Select-Object -First 1
                if (-not $shell) { Start-Sleep -Seconds 5 }
            }
            if ($shell) { Send $Icon.on 'pc.on' (J 'cold boot' "signed in after $([int]($shell.StartTime - $boot).TotalSeconds) s") }
            else { Send $Icon.on 'pc.on' (J 'cold boot' 'nobody signed in after 2 min (auto-login?)') }
        }
        'wake'   {
            # Kernel-Power 42 ("entering sleep") has the right time; 107 ("resumed") does not.
            $slept = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 42 } -MaxEvents 1 -ErrorAction SilentlyContinue
            # Power-Troubleshooter 1 (the event that started this task) names the wake source.
            $ev = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Power-Troubleshooter'; Id = 1 } -MaxEvents 1 -ErrorAction SilentlyContinue
            $src = if ($ev -and $ev.Properties.Count -gt 14) { "$($ev.Properties[14].Value)" } else { '' }
            $by = switch -Regex ($src) {
                'GbE|Ethernet|Network|LAN|Wi-?Fi|Wireless' { 'by network (Wake-on-LAN)'; break }
                'Power Button' { 'by the power button'; break }
                'Keyboard|Mouse|HID|USB Input' { 'by keyboard or mouse'; break }
                '^$' { ''; break }
                default { "by $src" }
            }
            Send $Icon.wake 'pc.wake' (J $(if ($slept) { "asleep since $($slept.TimeCreated.ToString('HH:mm'))" }) $by)
        }
        'shutdown' {
            if ((Seconds-Since 'selfoff') -le 120) { break }  # events.ps1 shut down itself and said so
            # User32 1074 (the event that started this task): process, reason, type, comment, user.
            $ev = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'User32'; Id = 1074 } -MaxEvents 1 -ErrorAction SilentlyContinue
            if (-not $ev) { Send $Icon.off 'pc.off' 'shutdown or restart'; break }
            $proc = "$($ev.Properties[0].Value)"; $type = "$($ev.Properties[4].Value)"; $user = ("$($ev.Properties[6].Value)" -split '\\')[-1]
            $who = switch -Regex ($proc) {
                'MoUsoCoreWorker|TrustedInstaller|usoclient|wuauclt|UpdateOrchestrator' { 'Windows Update'; break }
                'StartMenuExperienceHost|explorer\.exe' { "Start menu, $user"; break }
                'winlogon' { "sign-out screen, $user"; break }
                default { $exe = Split-Path ($proc -replace ' \(.*$', '') -Leaf; "$exe, $user" }
            }
            Send $Icon.off 'pc.off' (J $type $who)
        }
        'apollo' {
            # Service Control Manager 7031/7034: the Apollo service crashed. Windows may restart it.
            Start-Sleep -Seconds 30
            $svc = Get-Service ApolloService -ErrorAction SilentlyContinue
            $state = if ($svc -and $svc.Status -eq 'Running') { 'restarted by Windows' } else { 'still stopped: no streaming until it runs' }
            Send $Icon.err 'apollo.down' (J 'Apollo service crashed' $state)
        }
        'watch'    { Watch-Plan }
        'test'     { $s = Settings; Send $Icon.ok 'events.ok' "auto-sleep: $($s.autosleep)" }
        default  { Log "unknown event" }
    }
} catch {
    Log "error: $($_.Exception.Message)"
}
