# Turn on Windows automatic sign-in for the current user (works with a Microsoft account).
#
# Checks the password first, then hands it to Sysinternals Autologon (stores it as an LSA secret,
# not in plain text). The password must be the Microsoft ACCOUNT password, not the Windows Hello PIN.
#
# Run as the user who should be signed in, elevated (an admin SSH session counts):
#   powershell -ExecutionPolicy Bypass -File set-autologon.ps1          # prompts for the password
#   <password on stdin> | powershell -ExecutionPolicy Bypass -File set-autologon.ps1
#   ... -Disable                                                         # turn it off again
# Needs Autologon: winget install Microsoft.Sysinternals.Autologon
param([switch]$Disable, [switch]$SkipCheck)
$ErrorActionPreference = 'Stop'

$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'

function Find-Autologon {
    $cmd = Get-Command Autologon64.exe, autologon.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) {
        $item = Get-Item $cmd.Source
        if ($item.Target) { return [string]$item.Target }   # winget link -> real exe (the link may open the GUI)
        return $cmd.Source
    }
    $pkg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter 'Autologon64.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($pkg) { return $pkg.FullName }
    throw 'Autologon not found. Install it: winget install Microsoft.Sysinternals.Autologon'
}

if ($Disable) {
    Set-ItemProperty $winlogon AutoAdminLogon '0'
    Write-Host 'Automatic sign-in disabled (AutoAdminLogon=0).'
    exit 0
}

# Microsoft account -> user = the account e-mail, domain = MicrosoftAccount. Local account -> name + computer.
$msa = Get-ChildItem 'HKCU:\Software\Microsoft\IdentityCRL\UserExtendedProperties' -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty PSChildName
if ($msa) { $user = $msa; $domain = 'MicrosoftAccount' } else { $user = $env:USERNAME; $domain = $env:COMPUTERNAME }
Write-Host "Account: $domain\$user"

if ([Console]::IsInputRedirected) {
    $plain = [Console]::In.ReadToEnd().TrimEnd("`r", "`n")
} else {
    $sec = Read-Host 'Password (Microsoft account password, NOT the PIN)' -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
if (-not $plain) { throw 'Empty password.' }

if (-not $SkipCheck) {
    Add-Type -Namespace TvWake -Name Native -MemberDefinition @'
[DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern bool LogonUser(string user, string domain, string password, int logonType, int provider, out System.IntPtr token);
[DllImport("kernel32.dll")] public static extern bool CloseHandle(System.IntPtr h);
'@
    $ok = $false
    # 8 = NETWORK_CLEARTEXT (what Autologon itself uses), 2 = INTERACTIVE
    foreach ($try in @(@($user, $domain, 8), @($user, $null, 8), @($user, $domain, 2))) {
        $tok = [IntPtr]::Zero
        if ([TvWake.Native]::LogonUser($try[0], $try[1], $plain, $try[2], 0, [ref]$tok)) {
            [void][TvWake.Native]::CloseHandle($tok); $ok = $true; break
        }
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($err -eq 1326) { break }   # ERROR_LOGON_FAILURE: wrong user/password, no point retrying
    }
    if (-not $ok) {
        throw "Password check failed (Win32 error $err; 1326 = wrong password). Nothing changed. " +
              'Use the Microsoft account password, not the PIN. -SkipCheck forces it.'
    }
    Write-Host 'Password OK.'
}

$exe = Find-Autologon
& $exe $user $domain $plain /accepteula | Out-Null
$plain = $null

# Autologon sets these; make sure nothing limits the number of sign-ins.
Remove-ItemProperty $winlogon AutoLogonCount -ErrorAction SilentlyContinue
$w = Get-ItemProperty $winlogon
Write-Host "AutoAdminLogon=$($w.AutoAdminLogon) DefaultUserName=$($w.DefaultUserName) DefaultDomainName=$($w.DefaultDomainName)"
if ($w.AutoAdminLogon -ne '1') { throw 'Autologon did not set AutoAdminLogon=1.' }
Write-Host 'Automatic sign-in is on. It takes effect at the next boot.'
