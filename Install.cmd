@echo off
rem SPDX-License-Identifier: Unlicense
rem Start the overpressure installer on Windows: double-click this file.
rem
rem It fetches the pinned uv into cache\uv (checked by its SHA-256), which fetches Python 3.12 and
rem CustomTkinter into cache\ and opens the window. Everything stays inside this folder: nothing
rem is installed on the computer, and no administrator rights are needed.
setlocal EnableExtensions
cd /d "%~dp0"

set "UV_VERSION=0.13.4"
set "UV_ASSET=uv-x86_64-pc-windows-msvc.zip"
set "UV_SHA256=ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225"
set "ARCH=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "ARCH=%PROCESSOR_ARCHITEW6432%"
if /i "%ARCH%"=="ARM64" set "UV_ASSET=uv-aarch64-pc-windows-msvc.zip"
if /i "%ARCH%"=="ARM64" set "UV_SHA256=c4bc5a9534b2bb6a12fe29ea748445e1a6f9cb7bb275211c4fb74c52ecf317a5"
set "UV_URL=https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/%UV_ASSET%"
set "UV_ZIP=%CD%\cache\uv\%UV_ASSET%"
set "UV_DIR=%CD%\cache\uv\%UV_VERSION%-%ARCH%"

rem The download, its hash and the unpacking: PowerShell's own commands, the paths through the environment.
rem Windows PowerShell finds its own modules only: a PowerShell 7 that started this would lend it its own.
set "OUTER_PSMODULEPATH=%PSModulePath%"
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; function Test-Zip { (Test-Path -LiteralPath $env:UV_ZIP) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $env:UV_ZIP).Hash -eq $env:UV_SHA256) }; if (-not (Test-Zip)) { New-Item -ItemType Directory -Force -Path (Split-Path -Parent $env:UV_ZIP) | Out-Null; Write-Host ('Downloading uv ' + $env:UV_VERSION + ' ...'); [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:UV_URL -OutFile $env:UV_ZIP; if (-not (Test-Zip)) { Remove-Item -LiteralPath $env:UV_ZIP -Force; Write-Host 'error: the downloaded uv does not match its pinned SHA-256, so it was deleted. Try again later.'; exit 3 }; if (Test-Path -LiteralPath $env:UV_DIR) { Remove-Item -LiteralPath $env:UV_DIR -Recurse -Force } }; if (-not (Test-Path -LiteralPath (Join-Path $env:UV_DIR 'uv.exe'))) { Expand-Archive -LiteralPath $env:UV_ZIP -DestinationPath $env:UV_DIR -Force }"
if errorlevel 1 goto failed
set "PSModulePath=%OUTER_PSMODULEPATH%"

set "UV_CACHE_DIR=%CD%\cache\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%CD%\cache\python"
set "UV_PROJECT_ENVIRONMENT=%CD%\cache\venv"
"%UV_DIR%\uv.exe" run --locked --no-dev --extra gui --managed-python --python 3.12 python -m overpressure gui
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo The installer could not start or stopped with an error (above).
pause
exit /b 1
