# Create desktop shortcuts "TV on" / "TV off" that run pc\tv-power.cmd.
# Run once (no admin needed):  powershell -ExecutionPolicy Bypass -File pc\make-shortcuts.ps1
$ErrorActionPreference = 'Stop'
$cmd = Join-Path $PSScriptRoot 'tv-power.cmd'
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell

$items = @(
    # ASCII names: Windows PowerShell 5.1 misreads non-ASCII in BOM-less UTF-8 scripts.
    # Rename the shortcuts on the desktop as you like afterwards.
    @{ Name = 'TV On';  Arg = 'on';  Icon = "$env:SystemRoot\System32\imageres.dll,190" },
    @{ Name = 'TV Off'; Arg = 'off'; Icon = "$env:SystemRoot\System32\shell32.dll,27" }
)
foreach ($i in $items) {
    $path = Join-Path $desktop ($i.Name + '.lnk')
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = "$env:SystemRoot\System32\cmd.exe"
    $lnk.Arguments = "/c `"$cmd`" $($i.Arg)"
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = $i.Icon
    $lnk.WindowStyle = 7   # start minimized
    $lnk.Description = "LG TV $($i.Arg) via the helper"
    $lnk.Save()
    Write-Host "created $path"
}
