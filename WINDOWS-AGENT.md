# Hookah Agent on Windows 10/11 x64

Extract `Hookah-Agent-Windows-1.0.0.zip` into its own directory. The visible files
are **Hookah-Agent.exe**, **README.md**, **HELP.md**, and **config.json**. Python
code, dependency lists, service integration, and detailed documentation live
under the hidden `.hookah/` support tree.

## Foreground Agent

Install **Python 3.10+**, then in PowerShell:

```powershell
.\Hookah-Agent.exe setup
.\Hookah-Agent.exe --version
.\Hookah-Agent.exe --help
.\Hookah-Agent.exe config show
.\Hookah-Agent.exe
```

Setup creates `.hookah\environment` and installs the Agent dependencies. The
native x64 console launcher prefers `HOOKAH_PYTHON`, its private environment,
`py -3`, then `python`. It forwards arguments and exit status and handles spaces,
Unicode, quotes, and trailing backslashes. Version/help work before Python setup.

The visible `config.json` initially uses UDP 1161 and loopback-only Manager
access. Set `allowed_managers` to include the actual LAN Manager's address/CIDR.
Sampling commands use the same executable:

```powershell
.\Hookah-Agent.exe config set --interval ifInOctets 120
.\Hookah-Agent.exe config set --sample-interval 10
.\Hookah-Agent.exe config show
```

Sampling reloads live. Listening, key, community, and Manager-access changes
take effect after restarting. The foreground Agent needs no administrator
privileges when using UDP 1161. Net-SNMP clients are needed on the Manager,
while the Agent uses its Python protocol implementation.

## Native auto-start service

Use a system-wide Python installation readable by the service account.
Open **administrator PowerShell** in the extracted package directory:

```powershell
.\Hookah-Agent.exe service install `
    -PythonExe "C:\Program Files\Python312\python.exe" `
    -ManagerAddresses "192.168.1.10/32,127.0.0.1/32" `
    -Port 1161 -SampleInterval 1 -ReadCommunity public -WriteCommunity lab-write
```

The service installer creates grouped `.hookah\app`, `.hookah\dependencies`,
and `.hookah\environment` folders under `C:\Program Files\Hookah Agent`. It
installs pywin32, gives LocalService access, creates the persistent profile at
`C:\ProgramData\Hookah\Agent\snmp_agent_config.json`, configures a restricted
inbound UDP firewall rule, and starts **HookahSnmpAgent** with automatic startup
and restart-on-failure.

Optional `-RsaPrivateKey PATH` and `-PeerPublicKey PATH` configure explicitly
transferred encryption keys. Sample intervals are numeric seconds.

```powershell
Get-Service HookahSnmpAgent
Restart-Service HookahSnmpAgent
Get-Content "C:\ProgramData\Hookah\Agent\logs\agent.log" -Tail 50
.\Hookah-Agent.exe config set `
    --config "C:\ProgramData\Hookah\Agent\snmp_agent_config.json" --interval cpu 10
```

The service profile is separate from the portable foreground `config.json`.
Logs rotate at 2 MiB with three backups. SCM diagnostics are also written to
the Windows Application event log. For removal, run in administrator PowerShell:

```powershell
.\Hookah-Agent.exe service uninstall
```

This removes the service and its firewall rule, retaining application/data files.
In the developer source tree, the PowerShell installer/uninstaller are also
available directly under `windows/`.

## Verification

Automated tests cover real Windows console launcher behavior on Windows/Wine,
PowerShell syntax, Windows-style collectors, mocked SCM/registry operations,
quoted service paths, and stop-event lifecycle behavior. Native SCM deployment
is checked on the target Windows system with the status/log commands above.
