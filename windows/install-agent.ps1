#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$PythonExe = "python",
    [string]$InstallDirectory = (Join-Path $env:ProgramFiles "Hookah Agent"),
    [string]$DataDirectory = (Join-Path $env:ProgramData "Hookah\Agent"),
    [string[]]$ManagerAddresses = @("127.0.0.1/32"),
    [ValidateRange(1, 65535)][int]$Port = 1161,
    [double]$SampleInterval = 1,
    [string]$ReadCommunity = "public",
    [string]$WriteCommunity = "",
    [string]$RsaPrivateKey = "",
    [string]$PeerPublicKey = ""
)

$ErrorActionPreference = "Stop"
$source = Split-Path $PSScriptRoot -Parent

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Run this installer on Windows 10/11."
}
if ($SampleInterval -le 0 -or [double]::IsNaN($SampleInterval) -or [double]::IsInfinity($SampleInterval)) {
    throw "SampleInterval must be a finite positive number in seconds."
}
if (Get-Service -Name HookahSnmpAgent -ErrorAction SilentlyContinue) {
    throw "HookahSnmpAgent is already installed. Use the service CLI to manage it."
}

$resolvedPython = (Get-Command $PythonExe -ErrorAction Stop).Source
Invoke-Checked $resolvedPython @("-c", "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'")
New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DataDirectory "keys") -Force | Out-Null

$files = @("snmp_agent.py", "snmp_protocol.py", "snmp_mib.py", "snmp_cli_help.py", "snmp_crypto.py", "snmp_agent_windows_service.py", "requirements-agent.txt", "requirements-windows-agent.txt")
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $source $file) -Destination (Join-Path $InstallDirectory $file) -Force
}

$venv = Join-Path $InstallDirectory ".venv"
Invoke-Checked $resolvedPython @("-m", "venv", $venv)
$servicePython = Join-Path $venv "Scripts\python.exe"
$agent = Join-Path $InstallDirectory "snmp_agent.py"
$service = Join-Path $InstallDirectory "snmp_agent_windows_service.py"
Invoke-Checked $servicePython @("-m", "pip", "install", "-r", (Join-Path $InstallDirectory "requirements-windows-agent.txt"))

$configuration = Join-Path $DataDirectory "snmp_agent_config.json"
if (-not (Test-Path -LiteralPath $configuration)) {
    Invoke-Checked $servicePython @($agent, "config", "init", "--config", $configuration)
}
Invoke-Checked $servicePython @($agent, "config", "set", "--config", $configuration,
    "--set", "port", "$Port", "--sample-interval", $SampleInterval.ToString([Globalization.CultureInfo]::InvariantCulture))

# Set arrays through Python argv to avoid PowerShell 5.1 native JSON quoting.
$setManagers = "import sys; from pathlib import Path; from snmp_agent import update_config_file; update_config_file(Path(sys.argv[1]), {'read_community': sys.argv[2], 'allowed_managers': sys.argv[3:]})"
Push-Location $InstallDirectory
try {
    Invoke-Checked $servicePython (@("-c", $setManagers, $configuration, $ReadCommunity) + $ManagerAddresses)
} finally {
    Pop-Location
}
if ($WriteCommunity) {
    $setWrite = "import sys; from pathlib import Path; from snmp_agent import update_config_file; update_config_file(Path(sys.argv[1]), {'write_community': sys.argv[2]})"
    Push-Location $InstallDirectory
    try { Invoke-Checked $servicePython @("-c", $setWrite, $configuration, $WriteCommunity) }
    finally { Pop-Location }
}
if ($RsaPrivateKey) {
    $privateDestination = Join-Path $DataDirectory "keys\manager_private.pem"
    Copy-Item -LiteralPath (Resolve-Path $RsaPrivateKey).Path -Destination $privateDestination -Force
    $keyArguments = @($agent, "config", "set", "--config", $configuration,
        "--encryption", "on", "--rsa-private-key", $privateDestination)
    if ($PeerPublicKey) {
        $publicDestination = Join-Path $DataDirectory "keys\peer_public.pem"
        Copy-Item -LiteralPath (Resolve-Path $PeerPublicKey).Path -Destination $publicDestination -Force
        $keyArguments += @("--rsa-peer-public-key", $publicDestination)
    }
    Invoke-Checked $servicePython $keyArguments
} elseif ($PeerPublicKey) {
    throw "PeerPublicKey requires RsaPrivateKey."
}

# SYSTEM, Administrators, and LocalService own the persistent configuration/key area.
Invoke-Checked "icacls.exe" @($DataDirectory, "/inheritance:r", "/grant:r",
    "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-19:(OI)(CI)M", "/T")
Invoke-Checked "icacls.exe" @($InstallDirectory, "/grant", "*S-1-5-19:(OI)(CI)RX", "/T")

$firewallName = "Hookah SNMP Agent"
if (Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue) {
    throw "A Hookah firewall rule already exists; review it before installing the service."
}
New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow `
    -Protocol UDP -LocalPort $Port -RemoteAddress $ManagerAddresses -Profile Any | Out-Null
try {
    Invoke-Checked $servicePython @($service, "install", "--config", $configuration, "--startup", "auto")
    & sc.exe failure HookahSnmpAgent reset= 86400 actions= restart/5000/restart/10000/restart/30000
    if ($LASTEXITCODE -ne 0) { throw "Could not configure Windows service recovery." }
    Invoke-Checked $servicePython @($service, "start")
} catch {
    Write-Error $_
    throw
}
Write-Host "HookahSnmpAgent installed and running. Config: $configuration"
Write-Host "Logs: $(Join-Path $DataDirectory 'logs\agent.log')"
