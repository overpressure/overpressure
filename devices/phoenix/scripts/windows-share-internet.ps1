# Share this PC's internet with the PICO 4 over its USB link (docs/userspace.md#internet-over-usb).
#
# Usage (an elevated PowerShell 7, with the headset booted and its link up):
#   pwsh -File scripts/windows-share-internet.ps1 [-Internet <adapter name>]
#   pwsh -File scripts/windows-share-internet.ps1 -Off
#
# Windows' internet connection sharing (ICS) normally renumbers the shared adapter to
# 192.168.137.1; ScopeAddress keeps it at the PC's address on the link (172.16.42.2), where the
# headset expects its gateway and DNS server, so SSH and the dev tools keep working. ICS shares
# one connection on the whole PC: any sharing already enabled is turned off first. -Off turns
# sharing off and restores the default scope.
param(
    [string]$Internet,
    [switch]$Off,
    [string]$HostAddress = '172.16.42.2'
)
$ErrorActionPreference = 'Stop'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this in an elevated PowerShell (Run as administrator).'
}

$share = New-Object -ComObject HNetCfg.HNetShare
function Get-SharingConfig([string]$name) {
    foreach ($c in $share.EnumEveryConnection) {
        if ($share.NetConnectionProps($c).Name -eq $name) {
            return $share.INetSharingConfigurationForINetConnection($c)
        }
    }
    throw "No network connection named '$name'."
}

foreach ($c in $share.EnumEveryConnection) {
    $config = $share.INetSharingConfigurationForINetConnection($c)
    if ($config.SharingEnabled) {
        Write-Host "Turning off sharing on '$($share.NetConnectionProps($c).Name)'"
        $config.DisableSharing()
    }
}

$params = 'HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters'
if ($Off) {
    Set-ItemProperty $params ScopeAddress '192.168.137.1'
    Set-ItemProperty $params ScopeAddressBackup '192.168.137.1'
    Write-Host 'Sharing is off; the default ICS scope (192.168.137.1) is restored.'
    return
}

$headset = Get-NetAdapter | Where-Object InterfaceDescription -like 'UsbNcm Host Device*' | Select-Object -First 1
if (-not $headset) {
    throw 'No headset link (UsbNcm Host Device): boot the headset and connect it first.'
}
if (-not $Internet) {
    # The adapter with this PC's default route, not the headset's link.
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' |
        Where-Object InterfaceIndex -ne $headset.ifIndex | Sort-Object RouteMetric | Select-Object -First 1
    if (-not $route) { throw 'This PC has no default route to share; name the adapter with -Internet.' }
    $Internet = $route.InterfaceAlias
}

Set-ItemProperty $params ScopeAddress $HostAddress
Set-ItemProperty $params ScopeAddressBackup $HostAddress
(Get-SharingConfig $Internet).EnableSharing(0)        # ICSSHARINGTYPE_PUBLIC
(Get-SharingConfig $headset.Name).EnableSharing(1)    # ICSSHARINGTYPE_PRIVATE
Write-Host "Sharing '$Internet' with the headset on '$($headset.Name)' at $HostAddress."
