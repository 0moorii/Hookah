# Hookah Manager on Windows 10/11

The Manager distribution includes executable `Hookah-Manager.cmd` and
`Setup-Manager.cmd` launchers. They work from Command Prompt or PowerShell and
support directories containing spaces. Install **Python 3.10+** and the
**Net-SNMP Windows command-line clients** (`snmpget.exe`, `snmpwalk.exe`, and
`snmpset.exe`) on the monitoring machine.

## Set up the extracted Manager ZIP

Extract `Hookah-Manager-1.0.0.zip` to its own directory. In PowerShell there:

```powershell
.\Setup-Manager.cmd
```

If Net-SNMP is not on PATH, specify the directory containing the executables:

```powershell
.\Setup-Manager.cmd -NetSnmpDirectory "C:\Program Files\Net-SNMP\bin"
```

An explicit Python installation can also be selected:

```powershell
.\Setup-Manager.cmd -PythonExe "C:\Program Files\Python312\python.exe" `
    -NetSnmpDirectory "C:\Program Files\Net-SNMP\bin"
```

Setup creates a local `.venv`, installs `requirements-manager.txt`, preserves
an existing Manager profile, optionally sets its `snmp_bin`, and runs `doctor`.
Administrator privileges are not needed for this foreground installation.
The setup launcher applies PowerShell execution policy for its own process.

## Launch

```powershell
.\Hookah-Manager.cmd --version
.\Hookah-Manager.cmd doctor
.\Hookah-Manager.cmd
.\Hookah-Manager.cmd get --target udp:192.168.1.20:1161 `
    --community public --oid .1.3.6.1.2.1.1.1.0
.\Hookah-Manager.cmd capture --target udp:192.168.1.20:1161 `
    --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
.\Hookah-Manager.cmd plot cpu.csv --output plots\cpu.png --no-show
```

Use `snmp_manager_config.json` for the target/community, polling defaults,
Net-SNMP directory, and optional encryption profile. JSON Windows paths use
escaped backslashes, e.g. `"C:\\Program Files\\Net-SNMP\\bin"`; forward slashes
also work. CLI paths must be quoted when they contain spaces. Settings and
all command options are documented in `README-manager.md`.

The launcher's interpreter order is `HOOKAH_PYTHON`, adjacent
`.venv\Scripts\python.exe`, `py -3`, then `python`. It forwards command arguments
and the application's exit status. Python/module/configuration paths are based
on the launcher's own directory; CSV/PNG CLI paths remain relative to the caller.

The Agent ZIP has its own launcher, setup, configuration, and Windows service
installer. Run the Agent on the monitored machine and permit this Manager's
source address in the Agent profile.

## Platform verification

The release suite checks PowerShell syntax, setup helper behavior, Windows-style
metric data, and mocked native Agent service APIs. Windows-only launcher tests
execute the `.cmd` files when the suite runs on Windows. A release built on Linux
records that native Windows execution was unavailable in `VALIDATION.json`.
On the target Windows system, `Setup-Manager.cmd` followed by the `doctor`, GET,
capture, and plot commands above is the native deployment smoke check.
