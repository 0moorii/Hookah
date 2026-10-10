#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$NetSnmpDirectory = ""
)

. (Join-Path $PSScriptRoot "setup-common.ps1")
$root = Split-Path $PSScriptRoot -Parent
if ($NetSnmpDirectory) {
    $NetSnmpDirectory = (Resolve-Path -LiteralPath $NetSnmpDirectory -ErrorAction Stop).Path
    foreach ($tool in @("snmpget.exe", "snmpwalk.exe", "snmpset.exe")) {
        if (-not (Test-Path -LiteralPath (Join-Path $NetSnmpDirectory $tool) -PathType Leaf)) {
            throw "Missing Net-SNMP tool: $tool in $NetSnmpDirectory"
        }
    }
}
Initialize-HookahEnvironment $PythonExe (Join-Path $root ".venv") (Join-Path $root "requirements-manager.txt")
$python = Join-Path $root ".venv\Scripts\python.exe"
$manager = Join-Path $root "snmp_data_collector_v12.py"
$config = Join-Path $root "snmp_manager_config.json"
if (-not (Test-Path -LiteralPath $config)) {
    Invoke-HookahChecked $python @($manager, "config", "init", "--config", $config)
}
if ($NetSnmpDirectory) {
    # Python argv avoids Windows PowerShell 5.1 JSON/path quoting differences.
    $update = "import json,sys; from pathlib import Path; from snmp_manager_config import config_from_document; p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8-sig')); d['snmp_bin']=sys.argv[2]; config_from_document(d,p.parent); p.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')"
    Push-Location $root
    try { Invoke-HookahChecked $python @("-c", $update, $config, $NetSnmpDirectory) }
    finally { Pop-Location }
}
Invoke-HookahChecked $python @($manager, "doctor", "--config", $config)
Write-Host "Hookah Manager is ready. Run Hookah-Manager.cmd."
