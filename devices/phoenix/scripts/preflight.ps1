<#
.SYNOPSIS
  Run the stock-headset userspace preflight and preserve its exit status.
#>
param([string]$RootfsTar, [string]$Serial)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))  # the repository
if (-not $RootfsTar) {
    $latest = Get-ChildItem (Join-Path $root 'out\pico4-poc-*-rootfs.tar') -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw 'No rootfs tar in out\ - run: python -m overpressure build' }
    $RootfsTar = $latest.FullName
}
$env:PYTHONPATH = $root
$arguments = @('-m', 'overpressure', '--device', 'phoenix', 'hwtest', 'preflight', '--rootfs', $RootfsTar)
if ($Serial) { $arguments += @('--serial', $Serial) }
& python @arguments
exit $LASTEXITCODE
