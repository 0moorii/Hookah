# Choosing and running Hookah

1. Choose `Agent` for the monitored device or `Manager` for the monitoring host.
2. Choose the `Linux` or `Windows` folder inside that component.
3. Install Python 3.10+ and run the launcher with `setup`.
4. Edit its `config.json` for your network, then run the launcher normally.

Linux examples:

```sh
./Agent/Linux/Hookah-Agent.sh setup
./Agent/Linux/Hookah-Agent.sh
./Manager/Linux/Hookah-Manager.sh setup
./Manager/Linux/Hookah-Manager.sh doctor
./Manager/Linux/Hookah-Manager.sh --help
```

Windows examples in PowerShell:

```powershell
.\Agent\Windows\Hookah-Agent.exe setup
.\Agent\Windows\Hookah-Agent.exe
.\Manager\Windows\Hookah-Manager.exe setup --snmp-bin "C:/Program Files/Net-SNMP/bin"
.\Manager\Windows\Hookah-Manager.exe doctor
.\Manager\Windows\Hookah-Manager.exe --help
```

Each component's `HELP.md` describes its commands. Detailed guides and service
scripts live inside its `.hookah/` support folder. Implementation files are
grouped under `.hookah/app/`, and setup creates `.hookah/environment/`.

For testing or rebuilding, use the complete developer project in
`.hookah/source/`; its `TESTING.md` explains the unit, integration, smoke, and
platform-launcher verification commands.
