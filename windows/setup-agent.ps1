#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$WindowsServiceSupport
)

. (Join-Path $PSScriptRoot "setup-common.ps1")
$root = Split-Path $PSScriptRoot -Parent
$requirements = if ($WindowsServiceSupport) { "requirements-windows-agent.txt" } else { "requirements-agent.txt" }
Initialize-HookahEnvironment $PythonExe (Join-Path $root ".venv") (Join-Path $root $requirements)
$python = Join-Path $root ".venv\Scripts\python.exe"
$agent = Join-Path $root "snmp_agent.py"
$config = Join-Path $root "snmp_agent_config.json"
if (-not (Test-Path -LiteralPath $config)) {
    Invoke-HookahChecked $python @($agent, "config", "init", "--config", $config)
}
Invoke-HookahChecked $python @($agent, "config", "show", "--config", $config)
Write-Host "Hookah Agent is ready. Run Hookah-Agent.cmd. See WINDOWS-AGENT.md for service installation."
