# Hookah Agent on Windows 10/11 x64

Extract `Hookah-Agent-Windows-1.0.0-2.zip` into its own directory. The visible files
are **Hookah-Agent.exe**, **README.md**, **HELP.md**, and **config.json**. Python
code, dependency lists, service integration, and detailed documentation live
under the hidden `.hookah/` support tree.

## Foreground Agent

Python and the Agent dependencies are included. In PowerShell:

```powershell
.\Hookah-Agent.exe --version
.\Hookah-Agent.exe --help
.\Hookah-Agent.exe config show
.\Hookah-Agent.exe
```

`.hookah\environment` contains portable Python, PySNMP, psutil, cryptography, and
pywin32. The x64 console launcher runs this bundled interpreter directly. No
installation or internet connection is required; `setup` only verifies the
offline bundle. `HOOKAH_PYTHON` is an explicit developer override. Arguments,
Unicode paths, quotes, and exit statuses are preserved.

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

Open **administrator PowerShell** in the extracted package directory:

```powershell
.\Hookah-Agent.exe service install `
    -ManagerAddresses "192.168.1.10/32,127.0.0.1/32" `
    -Port 1161 -SampleInterval 1 -ReadCommunity public -WriteCommunity lab-write
```

The service installer creates grouped `.hookah\app`, `.hookah\dependencies`,
and `.hookah\environment` folders under `C:\Program Files\Hookah Agent`. It
copies the included runtime (including pywin32), gives LocalService access, creates the persistent profile at
`C:\ProgramData\Hookah\Agent\snmp_agent_config.json`, configures a restricted
inbound UDP firewall rule, and starts **HookahSnmpAgent** with automatic startup
and restart-on-failure. No dependency download is needed. An explicit `-PythonExe`
can still select an external full Python installation for a custom service environment.

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
