# Installs (or removes) the Trust Torix per-key lighting plug-in for ASUS Aura (Armoury Crate / LightingService).
#   .\install_aura_hal.ps1             install
#   .\install_aura_hal.ps1 -Uninstall  remove
# Needs the PATCHED keyboard firmware (see ..\README.md). Registration mirrors the ASUS AacHalSample installer: a COM class (both
# registry views) plus an entry in the third-party HAL list that LightingService enumerates.
# Nothing is assumed about where Windows or ASUS software is installed: folders come from Windows, and if ASUS Aura is not found
# the script changes nothing and exits with code 2.
param([switch]$Uninstall)

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Uninstall) { $a += '-Uninstall' }
    $p = Start-Process powershell -Verb RunAs -ArgumentList $a -Wait -PassThru
    exit $p.ExitCode
}

$ErrorActionPreference = 'Stop'
$clsid = '{28E19014-8549-4CFC-9DEA-6087F445C737}'
$dir   = Join-Path $env:ProgramFiles 'TrustRGB'         # where our two DLLs are copied (the folder name is ours; the drive/location comes from Windows)
$cfg   = Join-Path $env:ProgramData  'TrustRGB'         # log and settings
$list  = 'CLSID\{109DC3E4-B9FF-4AF3-9008-AB13705D4E5F}\Instance\{E9BBD754-6CF4-492E-BA89-782177A2771B}\Instance'    # ASUS: third-party HAL list
$views = [ordered]@{ 'HKLM:\SOFTWARE\Classes' = 'x64'; 'HKLM:\SOFTWARE\WOW6432Node\Classes' = 'x86' }

# ASUS Aura present? Its HAL list exists in the 64-bit registry view and LightingService is installed.
$present = (Test-Path "HKLM:\SOFTWARE\Classes\$list") -and [bool](Get-Service -Name LightingService -ErrorAction SilentlyContinue)
if (-not $present -and -not $Uninstall) {
    Write-Host 'ASUS Aura (LightingService) was not found on this PC, so nothing was installed.'
    exit 2
}

$svc = Get-Service -Name LightingService -ErrorAction SilentlyContinue
$wasRunning = $svc -and $svc.Status -eq 'Running'
if ($wasRunning) { Write-Host 'Stopping ASUS LightingService ...'; Stop-Service LightingService -Force }

foreach ($root in $views.Keys) {
    Remove-Item "$root\CLSID\$clsid", "$root\$list\$clsid" -Recurse -Force -ErrorAction SilentlyContinue
}
if ($Uninstall) {
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'Removed.'
} else {
    foreach ($f in 'TrustAuraHal_x86.dll', 'TrustAuraHal_x64.dll') { if (-not (Test-Path (Join-Path $PSScriptRoot $f))) { throw "Missing $f next to this script" } }
    New-Item -ItemType Directory -Force $dir | Out-Null
    Copy-Item (Join-Path $PSScriptRoot 'TrustAuraHal_x86.dll'), (Join-Path $PSScriptRoot 'TrustAuraHal_x64.dll') $dir -Force
    foreach ($root in $views.Keys) {
        if (-not (Test-Path "$root\$list")) { continue }                 # a view without ASUS's list (no 32-bit Aura components) gets nothing
        $k = New-Item "$root\CLSID\$clsid\InprocServer32" -Force
        Set-ItemProperty "$root\CLSID\$clsid" '(default)' 'TrustAuraHal'
        Set-ItemProperty $k.PSPath '(default)' (Join-Path $dir "TrustAuraHal_$($views[$root]).dll")
        Set-ItemProperty $k.PSPath 'ThreadingModel' 'Both'
        $i = New-Item "$root\$list\$clsid" -Force
        @{ Description = 'Trust GXT 868 Torix per-key lighting'; Name = 'TrustAuraHal'; Manufacturer = 'TorixPatch';
           DeviceModel = 'GXT 868 Torix'; DeviceType = 'Keyboard'; Version = '1.0.0'; SpecVersion = '1.0.0' }.GetEnumerator() |
            ForEach-Object { Set-ItemProperty $i.PSPath $_.Key $_.Value }
        New-ItemProperty $i.PSPath 'Pluging' -PropertyType DWord -Value 0 -Force | Out-Null
    }
    # Gap between USB packets in microseconds: 2000 is clean with the patched firmware (the stock firmware needs 8000 or more).
    New-Item -ItemType Directory -Force $cfg | Out-Null
    if (-not (Test-Path (Join-Path $cfg 'torix_gap_us.txt'))) { Set-Content (Join-Path $cfg 'torix_gap_us.txt') '2000 2000' -Encoding ascii }
    Write-Host "Installed to $dir"
}
if ($wasRunning) { Start-Service LightingService }
# Armoury Crate only asks LightingService for its devices when it starts.
$ac = Get-Service -Name ArmouryCrateService -ErrorAction SilentlyContinue
if ($ac -and $ac.Status -eq 'Running') { Restart-Service ArmouryCrateService -Force -ErrorAction SilentlyContinue }
if (-not $Uninstall) { Write-Host "Done. Open Armoury Crate, go to Aura Sync and tick `"Trust GXT 868 Torix`". Log: $cfg\hal.log" }
exit 0
