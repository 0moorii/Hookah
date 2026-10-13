# Hookah @VERSION@

The release is grouped by application and operating system:

```text
Agent/
  Linux/          hookah-ag, README.md, HELP.md, config.json
  Windows/        Hookah-Agent.exe, README.md, HELP.md, config.json
Manager/
  Linux/          hookah-mg, README.md, HELP.md, config.json
  Windows/        Hookah-Manager.exe, README.md, HELP.md, config.json
README.md
HELP.md
.hookah/          source and release verification metadata
```

Choose the component and platform folder, then run its launcher. You may
copy that whole folder to another machine. Each folder has its own hidden
`.hookah/` support tree with application code, dependencies, detailed guides,
configuration templates, and metadata. The top-level `config.json` is editable.

Every component includes portable Python and its installed dependencies in
`.hookah/environment`. The Manager has an included SNMP client backend and
plotting libraries. No external Python, pip, or Net-SNMP installation is needed.
Windows launchers are native x64 console executables; Linux launchers are shell
scripts for x86-64 glibc Linux (glibc 2.28+). `setup` is an optional offline check.

Developer source and tests are under `.hookah/source/`. Release manifests,
checksums, and actual validation results are under `.hookah/metadata/`.
