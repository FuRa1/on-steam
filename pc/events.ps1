# PC events -> Telegram, and sleep when a stream is left paused with the TV off.
# Installed to C:\ProgramData\tvwake\ by pc/install-events.ps1; settings in events.json next to it.
#
# Called by:
#   Apollo global_prep_cmd   do: start     undo: end       (app launched / app closed)
#   Apollo global_state_cmd  do: resume    undo: pause     (first client connected / all disconnected)
#   Task Scheduler           boot, wake, shutdown          (Windows events)
#
# Apollo waits for its commands, so the hook only reads Apollo's variables and hands the work to a
# detached copy of itself (-Run). That copy is created through WMI: processes started any other way
# by a short-lived parent (Apollo hook, ssh) can be killed together with it.
param(
    [Parameter(Position = 0)][string]$Kind,  # not $Event: that is an automatic variable
    [switch]$Run,
    [string]$App = '',
    [string]$Client = ''
)
$ErrorActionPreference = 'Stop'
$Dir = 'C:\ProgramData\tvwake'
$LogFile = "$Dir\events.log"
$ApolloLog = 'C:\Program Files\Apollo\config\sunshine.log'
$TvPorts = @(3001, 9922)  # LG control protocol, dev mode ssh: open whenever the TV is on

function Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Kind] $msg"
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Settings {
    $s = Get-Content "$Dir\events.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $s.autosleep) { $s | Add-Member autosleep 'off' }
    $s
}

function Send([string]$text) {
    $s = Settings
    if (-not $s.telegram_bot_token -or -not $s.telegram_chat_id) { Log "no telegram settings: $text"; return }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $body = @{ chat_id = $s.telegram_chat_id; text = $text; disable_web_page_preview = $true } | ConvertTo-Json -Compress
    for ($i = 1; $i -le 3; $i++) {
        try {
            Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$($s.telegram_bot_token)/sendMessage" `
                -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 10 | Out-Null
            Log "sent: $text"; return
        } catch {
            Log "send attempt $i failed: $($_.Exception.Message)"   # the message never contains the URL
            Start-Sleep -Seconds (5 * $i)                            # e.g. network not up yet after wake
        }
    }
}

function Tv-On([string]$ip) {
    if (-not $ip) { return $true }  # unknown TV = assume on = never sleep
    if (Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue) { return $true }
    foreach ($p in $TvPorts) {
        $c = New-Object Net.Sockets.TcpClient
        try { if ($c.ConnectAsync($ip, $p).Wait(1500)) { return $true } } catch {} finally { $c.Dispose() }
    }
    $false
}

function Last-Session-Line {
    Select-String -Path $ApolloLog -Pattern 'New streaming session|Session resuming|Session pausing|CLIENT DISCONNECTED|[Tt]erminat' |
        Select-Object -Last 1 -ExpandProperty Line
}

function Mark([string]$name) { Set-Content "$Dir\last-$name" (Get-Date).ToString('o') }
# Values pass through the command line as base64 of "#" + value, so an empty value is never an empty
# argument (PowerShell -File refuses "-Client" without a value).
function Enc([string]$x) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("#$x")) }
function Dec([string]$x) { if (-not $x) { return '' }; [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($x)).Substring(1) }
function Seconds-Since([string]$name) {
    try { ((Get-Date) - [datetime](Get-Content "$Dir\last-$name" -ErrorAction Stop)).TotalSeconds } catch { 1e9 }
}

function Sleep-If-Tv-Off {
    $s = Settings
    if ($s.autosleep -eq 'off') { return }
    Start-Sleep -Seconds ([double]$s.autosleep_after_s + 5)  # let a quick reconnect happen first
    # Every check must say "leave it" or "sleep"; anything unexpected leaves the PC on.
    foreach ($i in 1..2) {
        if (Tv-On $s.tv_ip) { Log 'TV is on: not sleeping'; return }
        Start-Sleep -Seconds 5
    }
    $last = Last-Session-Line
    if ($last -notmatch 'Session pausing') { Log "stream not paused any more ($last): not sleeping"; return }
    if ($s.autosleep -ne 'on') { Send '🧪 Усыпил бы ПК: стрим на паузе, ТВ выключен (пробный режим)'; return }
    Send '😴 Стрим на паузе, ТВ выключен — усыпляю ПК'
    Log 'sleeping'
    & rundll32.exe powrprof.dll,SetSuspendState 0,1,0   # hibernation is off here, so this is S3
}

# ---- hook mode: hand over to a detached copy and return to Apollo at once -----------------------
# Must never fail: if a prep command fails, Apollo does not start the app.
if (-not $Run) {
    try {
        if ($Kind -eq 'start') { Mark 'start' }  # here, not in the worker: the resume hook follows at once
        $a = if ($env:APOLLO_APP_NAME) { $env:APOLLO_APP_NAME } else { $env:SUNSHINE_APP_NAME }
        $c = if ($env:APOLLO_CLIENT_NAME) { $env:APOLLO_CLIENT_NAME } else { $env:SUNSHINE_CLIENT_NAME }
        $cmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\events.ps1`" $Kind -Run -App $(Enc $a) -Client $(Enc $c)"
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
$what = if ($App) { ": $App" } else { '' }
$who = if ($Client) { " ($Client)" } else { '' }
$t = Get-Date -Format 'HH:mm'
try {
    switch ($Kind) {
        'start'  { Send "▶️ $t Стрим начался$what$who" }
        'resume' { if ((Seconds-Since 'start') -gt 20) { Send "▶️ $t Стрим продолжен$what$who" } }  # skip the resume that comes with a start
        'pause'  { Send "⏸ $t Клиент отключился, сессия на паузе$what"; Sleep-If-Tv-Off }
        'end'    { Send "⏹ $t Стрим завершён$what" }
        'boot'   { Send "💻 $t ПК включился" }
        'wake'   {
            # Kernel-Power 42 ("entering sleep") has the right time; 107 ("resumed") does not.
            $slept = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 42 } -MaxEvents 1 -ErrorAction SilentlyContinue
            $since = if ($slept) { " (спал с $($slept.TimeCreated.ToString('HH:mm')))" } else { '' }
            Send "☀️ $t ПК проснулся$since"
        }
        'shutdown' { Send "⏻ $t ПК выключается или перезагружается" }
        'test'     { $s = Settings; Send "✅ $t События ПК подключены (автосон: $($s.autosleep))" }
        default  { Log "unknown event" }
    }
} catch {
    Log "error: $($_.Exception.Message)"
}
