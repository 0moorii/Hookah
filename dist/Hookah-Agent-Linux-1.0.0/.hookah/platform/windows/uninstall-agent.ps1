#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$InstallDirectory = (Join-Path $env:ProgramFiles "Hookah Agent"))

$ErrorActionPreference = "Stop"
$python = Join-Path $InstallDirectory ".venv\Scripts\python.exe"
$service = Join-Path $InstallDirectory "snmp_agent_windows_service.py"
& $python $service remove
if ($LASTEXITCODE -ne 0) { throw "Service removal failed." }
Get-NetFirewallRule -DisplayName "Hookah SNMP Agent" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "HookahSnmpAgent removed. Configuration, keys, logs, and application files are retained."
