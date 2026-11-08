# One-time Windows setup for the gaming PC. Run in an ADMINISTRATOR PowerShell:
#   powershell -ExecutionPolicy Bypass -File pc\setup-windows.ps1
#
# What it does (each step prints what it changed):
#   1. Marks the wired network as Private.
#   2. Allows ping (ICMP echo) from the local subnet, so the helper sees the PC boot.
#   3. Turns Fast Startup off (Wake-on-LAN from "shut down" is unreliable with it).
#   4. Allows the network card to wake the PC (magic packet only).
#   5. Turns off "require sign-in when waking up".
#   6. Turns off "only allow Windows Hello sign-in" so automatic login can be set up.
#   7. Checks that the Apollo service starts automatically and Steam is in autostart.
# It does NOT store your password. Automatic login is set up afterwards with Microsoft's
# Sysinternals Autologon (the script tells you how).

#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Done($text) { Write-Host "    $text" -ForegroundColor Green }
function Note($text) { Write-Host "    $text" -ForegroundColor Yellow }

# The wired adapter that has the default route.
$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1
$nic = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex
Step "Network adapter: $($nic.Name) ($($nic.InterfaceDescription)), MAC $($nic.MacAddress)"

Step "1. Network profile -> Private"
Set-NetConnectionProfile -InterfaceIndex $nic.ifIndex -NetworkCategory Private
Done "$((Get-NetConnectionProfile -InterfaceIndex $nic.ifIndex).NetworkCategory)"

Step "2. Allow ping from the local network"
$ruleName = 'tv-wake: ping from LAN'
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol ICMPv4 -IcmpType 8 `
        -RemoteAddress LocalSubnet -Profile Private -Action Allow | Out-Null
    Done "firewall rule created"
} else { Done "firewall rule already exists" }

Step "3. Fast Startup off"
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -Value 0 -Type DWord
Done "HiberbootEnabled = 0"

Step "4. Network card may wake the PC"
& powercfg /deviceenablewake "$($nic.InterfaceDescription)" 2>$null | Out-Null
try {
    Set-NetAdapterPowerManagement -Name $nic.Name -WakeOnMagicPacket Enabled -ErrorAction Stop
    Done "Wake on Magic Packet enabled"
} catch { Note "could not set WakeOnMagicPacket via PowerShell ($($_.Exception.Message)); check Device Manager" }
Done ("wake-armed devices: " + ((& powercfg /devicequery wake_armed) -join ', '))

Step "5. No sign-in required after sleep"
& powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_NONE CONSOLELOCK 0
& powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_NONE CONSOLELOCK 0
& powercfg /SETACTIVE SCHEME_CURRENT
Done "CONSOLELOCK = 0 for the current power plan"

Step "6. Allow password sign-in (needed for automatic login)"
$pl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
if (Test-Path $pl) {
    Set-ItemProperty $pl -Name DevicePasswordLessBuildVersion -Value 0 -Type DWord
    Done "DevicePasswordLessBuildVersion = 0"
}

Step "7. Streaming + Steam autostart"
$svc = Get-CimInstance Win32_Service | Where-Object { $_.Name -match 'Apollo|SunshineService' } | Select-Object -First 1
if ($svc) {
    if ($svc.StartMode -ne 'Auto') { Set-Service -Name $svc.Name -StartupType Automatic; Done "$($svc.Name) set to Automatic" }
    else { Done "$($svc.Name): $($svc.State), starts automatically" }
} else { Note "Apollo/Sunshine service not found. Install Apollo: https://github.com/ClassicOldSong/Apollo/releases" }
$steamRun = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).Steam
if ($steamRun) { Done "Steam autostart: $steamRun" } else { Note "Steam is not in autostart: Steam > Settings > Interface > 'Run Steam when my computer starts'" }

Step "Remaining manual step: automatic Windows login"
Write-Host @"
    1. Download Autologon from Microsoft: https://learn.microsoft.com/sysinternals/downloads/autologon
       (or: winget install Microsoft.Sysinternals.Autologon)
    2. Run Autologon64.exe, enter your user name and password, press Enable.
       It stores the password encrypted (LSA secret), not in plain text.
    Note: anyone with physical access to this PC will be logged in automatically.
"@
Write-Host "`nAll done. Reboot once so Fast Startup = off takes effect." -ForegroundColor Cyan
