# Hookah @VERSION@

The release is grouped by application and operating system:

```text
Agent/
  Linux/          Hookah-Agent.sh, README.md, HELP.md, config.json
  Windows/        Hookah-Agent.exe, README.md, HELP.md, config.json
Manager/
  Linux/          Hookah-Manager.sh, README.md, HELP.md, config.json
  Windows/        Hookah-Manager.exe, README.md, HELP.md, config.json
README.md
HELP.md
.hookah/          source and release verification metadata
```

Choose the component and platform folder, then read its `README.md`. You may
copy that whole folder to another machine. Each folder has its own hidden
`.hookah/` support tree with application code, dependencies, detailed guides,
configuration templates, and metadata. The top-level `config.json` is editable.

Python 3.10+ is required for runtime/setup. Windows launchers are native x64
console executables; Linux launchers are executable shell scripts. Dependencies
are installed through the launcher's `setup` command into its private environment.
Net-SNMP clients are also required on the Manager machine.

Developer source and tests are under `.hookah/source/`. Release manifests,
checksums, and actual validation results are under `.hookah/metadata/`.
