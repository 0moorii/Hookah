# Hookah Manager on Windows 10/11 x64

Extract `Hookah-Manager-Windows-1.0.0.zip` into its own directory. The visible
files are **Hookah-Manager.exe**, **README.md**, **HELP.md**, and **config.json**.
The native console executable keeps application files in the hidden `.hookah/`
support tree and supports paths containing spaces and Unicode characters.

## Setup

Install **Python 3.10+** and the **Net-SNMP Windows command-line clients**
(`snmpget.exe`, `snmpwalk.exe`, and `snmpset.exe`). In PowerShell:

```powershell
.\Hookah-Manager.exe setup --snmp-bin "C:\Program Files\Net-SNMP\bin"
.\Hookah-Manager.exe doctor
.\Hookah-Manager.exe --version
```

If Net-SNMP is already on PATH, use `setup` without `--snmp-bin`.
Setup creates `.hookah\environment`, installs the grouped dependency list,
preserves an existing `config.json`, optionally records the Net-SNMP directory,
and runs diagnostics. Administrator privileges are unnecessary for foreground
setup. `HOOKAH_PYTHON` selects an explicit Python executable when needed.

## Run

```powershell
.\Hookah-Manager.exe
.\Hookah-Manager.exe --help
.\Hookah-Manager.exe config show
.\Hookah-Manager.exe get --target udp:192.168.1.20:1161 `
    --community public --oid .1.3.6.1.2.1.1.1.0
.\Hookah-Manager.exe capture --target udp:192.168.1.20:1161 `
    --oid .1.3.6.1.2.1.25.3.3.1.2 --duration 10 --output cpu.csv
.\Hookah-Manager.exe plot cpu.csv --output plots\cpu.png --no-show
```

The visible profile controls the target, community, polling defaults, tool
directory, and optional encryption profile. JSON paths use doubled backslashes
or forward slashes. Quote CLI paths containing spaces. The adjacent profile is
used even when the executable is started from another directory; `--config PATH`
or `HOOKAH_MANAGER_CONFIG` selects another profile.

Interpreter selection is `HOOKAH_PYTHON`, the private environment, `py -3`, then
`python`. Arguments and child exit statuses are preserved. Native `--version`
and top-level help work before Python setup. The executable is a launcher for
the private Python runtime; install Python before the first `setup` invocation.

## Verification

The release suite validates real PE32+ x64 console images and standard Windows
DLL dependencies. Windows/Wine tests execute version/help, Unicode and quoted
arguments, empty/trailing-backslash values, error messages, and exit-code
propagation. PowerShell scripts and service APIs have additional syntax/unit
checks. Native Windows deployment can be smoke-checked with `doctor`, GET,
capture, and plotting using the commands above.
