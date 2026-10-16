<#
.SYNOPSIS
  Boot the PICO 4 Linux POC image from RAM ("fastboot boot"); nothing is flashed.

.DESCRIPTION
  Needs Android platform-tools (adb, fastboot) in PATH and an unlocked
  bootloader. Reboots the headset into fastboot if it is running PICO OS,
  boots the image, then waits for the USB network (172.16.42.1).
  A plain reboot of the headset returns to PICO OS.

.EXAMPLE
  .\scripts\boot-poc.ps1
  .\scripts\boot-poc.ps1 -Image out\pico4-poc-5.13.8-global-sek.img
#>
param(
    [string]$Image
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not $Image) {
    $latest = Get-ChildItem (Join-Path $root 'out\pico4-poc-*.img') -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw 'No image in out\ - run: python build.py build' }
    $Image = $latest.FullName
}
if (-not (Get-Command fastboot -ErrorAction SilentlyContinue)) {
    throw 'fastboot not found: install Android platform-tools and add them to PATH'
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.9+ is required for the unlock-state check (device.py).'
}

if (Get-Command adb -ErrorAction SilentlyContinue) {
    $state = (& adb get-state 2>$null)
    if ($state -eq 'device') {
        Write-Host '>> headset is running PICO OS, rebooting into fastboot'
        & adb reboot bootloader
    }
}

Write-Host '>> waiting for the headset in fastboot mode'
for ($i = 0; $i -lt 60; $i++) {
    $devices = (& fastboot devices 2>$null)
    if ($devices) { break }
    Start-Sleep -Seconds 1
}
if (-not $devices) { throw 'No fastboot device found (cable? bootloader mode? WinUSB driver?)' }
Write-Host $devices

& python (Join-Path $root 'device.py') check-unlocked
if ($LASTEXITCODE -ne 0) {
    throw 'Unlock state is locked or unknown. See DEVICE.md before attempting to boot.'
}

Write-Host ">> fastboot boot $Image   (RAM only, nothing is written to the headset)"
& fastboot boot $Image
if ($LASTEXITCODE -ne 0) {
    throw ('fastboot boot failed. If the bootloader reports an unknown command, see README.md, ' +
           '"If fastboot boot is not available".')
}

Write-Host '>> waiting for the headset on 172.16.42.1 (up to 3 minutes)'
for ($i = 0; $i -lt 90; $i++) {
    if (Test-Connection -TargetName 172.16.42.1 -Count 1 -Quiet -TimeoutSeconds 1 -ErrorAction SilentlyContinue) {
        Write-Host ''
        Write-Host 'Linux is up:'
        Write-Host '  ssh root@172.16.42.1        (empty password)'
        Write-Host '  http://172.16.42.1/         (status, logs, hardware report)'
        exit 0
    }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 1
}
Write-Host ''
Write-Host 'No network yet. Check README.md > Troubleshooting: look for a "PICO4POC" USB drive'
Write-Host '(boot logs) and for an unknown "PICO 4 Linux POC" device in Device Manager.'
exit 1
