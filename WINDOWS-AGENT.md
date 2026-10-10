# Install Hookah Agent as a Windows 10/11 service

The Agent collects Windows host metrics through psutil and runs as a native
**Windows Service Control Manager** service. Net-SNMP utilities are needed by
the Manager, not by the Windows Agent.

## Requirements on the monitored Windows device

- Windows 10 or Windows 11.
- Python 3.10+; a system-wide, 64-bit Python 3.12 installation is recommended so
  the service account can read the Python runtime. The installer accepts its
  explicit `python.exe` path.
- Administrator PowerShell for service/firewall installation.
- Access to the Python package index to install `requirements-windows-agent.txt`.

## Transfer the install package

Build the package on the development/Manager machine:

```bash
python build_agent_package.py
```

This creates `dist/Hookah-Agent-Windows.zip`. Extract it to a folder on the other
Windows device. The package contains the Agent, service host, dependencies,
configuration example, installer/uninstaller, and these deployment guides.

If using encryption, generate the RSA keys on the Manager as documented in
`ENCRYPTION.md`, then explicitly transfer `manager_private.pem` to the Windows
device for the installer to copy into its configured key directory.

## Install and activate

Open **administrator PowerShell**, enter the extracted directory, then run:

```powershell
.\windows\install-agent.ps1 `
    -PythonExe "C:\Program Files\Python312\python.exe" `
    -ManagerAddresses @("192.168.1.10/32", "127.0.0.1/32") `
    -Port 1161 `
    -SampleInterval 1 `
    -ReadCommunity public `
    -WriteCommunity lab-write
```

For RSA-encrypted traffic, supply the transferred private PEM:

```powershell
.\windows\install-agent.ps1 `
    -PythonExe "C:\Program Files\Python312\python.exe" `
    -ManagerAddresses @("192.168.1.10/32", "127.0.0.1/32") `
    -Port 1161 -SampleInterval 1 `
    -ReadCommunity public -WriteCommunity lab-write `
    -RsaPrivateKey "C:\Downloads\manager_private.pem"
```

Replace the manager IP, communities, and paths. `SampleInterval` is seconds:
`120` means two minutes. Optional `-PeerPublicKey` supplies a separate peer's
public PEM; the shared-key setup derives the public key from the private PEM.

The installer:

1. Copies application files to `C:\Program Files\Hookah Agent`.
2. Creates a dedicated `.venv` and installs psutil, PySNMP, cryptography, and
   pywin32.
3. Creates/updates the JSON configuration under
   `C:\ProgramData\Hookah\Agent\snmp_agent_config.json` using the Agent CLI.
4. Copies an explicitly supplied RSA key and configures its file path.
5. Gives the service's **LocalService** account access to the application/data
   directories.
6. Adds an inbound UDP firewall rule limited to the supplied manager addresses.
7. Registers **HookahSnmpAgent** with automatic startup and restart-on-failure,
   then starts it.

The service uses its virtualenv's `python.exe` directly and registers a native
SCM dispatcher. It does not depend on the virtualenv's `pythonservice.exe`
bootstrap/DLL lookup. Application paths containing spaces are quoted.

## Check and manage the service

```powershell
Get-Service HookahSnmpAgent
Start-Service HookahSnmpAgent
Stop-Service HookahSnmpAgent
Restart-Service HookahSnmpAgent
Get-Content "C:\ProgramData\Hookah\Agent\logs\agent.log" -Tail 50
```

Logs rotate at 2 MiB with three backups. Startup/service errors are also written
to the Windows Application event log.

The native service CLI is available through its installed Python:

```powershell
$python = "C:\Program Files\Hookah Agent\.venv\Scripts\python.exe"
$service = "C:\Program Files\Hookah Agent\snmp_agent_windows_service.py"
& $python $service status
& $python $service restart
```

## Change collection periods without an editor

```powershell
$python = "C:\Program Files\Hookah Agent\.venv\Scripts\python.exe"
$agent = "C:\Program Files\Hookah Agent\snmp_agent.py"
$config = "C:\ProgramData\Hookah\Agent\snmp_agent_config.json"
& $python $agent config set --config $config --interval ifInOctets 120
& $python $agent config set --config $config --sample-interval 10
& $python $agent config show --config $config
```

Sampling changes reload live. The Agent service stays running during a
120-second collection period and notices configuration changes promptly. Stop
requests also wake the service loop promptly. Key/listening/community changes
take effect on restart.

For changes from the Manager device, use the remote CLI in `README-agent.md`,
adding the encryption profile from `ENCRYPTION.md` when encrypted transport is
enabled. A Manager can run on Linux or another machine with Net-SNMP installed.

## Remove the service

From the extracted package directory, in administrator PowerShell:

```powershell
.\windows\uninstall-agent.ps1
```

This stops/removes the service and its firewall rule. Application files,
configuration, keys, persisted metadata, and logs are retained.

## Platform verification

The automated tests verify Windows-style interface data, mocked SCM/registry
install/start/stop/remove operations, quoted service commands, and real Agent
shutdown through the service stop event. PowerShell installers are syntax-checked.
Actual SCM activation must be checked on the target Windows 10/11 device using
the status/log commands above; the development test host is Linux.
