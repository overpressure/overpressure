<#
.SYNOPSIS
  Run the stock-headset userspace preflight and preserve its exit status.
#>
param([string]$RootfsTar, [string]$Serial)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $RootfsTar) {
    $latest = Get-ChildItem (Join-Path $root 'out\pico4-poc-*-rootfs.tar') -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw 'No rootfs tar in out\ - run: python build.py build' }
    $RootfsTar = $latest.FullName
}
$arguments = @((Join-Path $root 'hardware_test.py'), 'preflight', '--rootfs', $RootfsTar)
if ($Serial) { $arguments += @('--serial', $Serial) }
& python @arguments
exit $LASTEXITCODE
