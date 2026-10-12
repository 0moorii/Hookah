# Hookah Manager on Windows 10/11 x64

Extract `Hookah-Manager-Windows-1.0.0.zip` into its own directory. The visible
files are **Hookah-Manager.exe**, **README.md**, **HELP.md**, and **config.json**.
The native console executable keeps application files in the hidden `.hookah/`
support tree and supports paths containing spaces and Unicode characters.

## Setup

Python, SNMP client support, encryption, and plotting dependencies are included.
No system Python or Net-SNMP installation is necessary. In PowerShell:

```powershell
.\Hookah-Manager.exe doctor
.\Hookah-Manager.exe --version
```

The installed runtime is already in `.hookah\environment`. `setup` is an optional
offline verification command that preserves `config.json`; it never downloads
dependencies. To use external Net-SNMP tools instead of the bundled PySNMP client,
set `snmp_bin` in the profile or pass `--snmp-bin PATH`. Administrator privileges
are unnecessary for foreground use. `HOOKAH_PYTHON` is a developer override.

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

The executable selects its bundled Python runtime automatically unless
`HOOKAH_PYTHON` explicitly overrides it. Arguments and exit statuses are preserved.
Run after extracting the whole ZIP, including the hidden `.hookah/` directory.

## Verification

The release suite validates real PE32+ x64 console images and standard Windows
DLL dependencies. Windows/Wine tests execute version/help, Unicode and quoted
arguments, empty/trailing-backslash values, error messages, and exit-code
propagation. PowerShell scripts and service APIs have additional syntax/unit
checks. Native Windows deployment can be smoke-checked with `doctor`, GET,
capture, and plotting using the commands above.
