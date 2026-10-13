# Hookah @VERSION@ — @PLATFORM@

This archive contains both applications for **@PLATFORM@ only**:

```text
Agent/
  @AGENT_LAUNCHER@
  README.md, HELP.md, config.json, .hookah/
Manager/
  @MANAGER_LAUNCHER@
  README.md, HELP.md, config.json, .hookah/
README.md
HELP.md
.hookah/          source and release verification metadata
```

Choose the Agent or Manager folder, then run its launcher. You may
copy that whole folder to another machine. Each folder has its own hidden
`.hookah/` support tree with application code, dependencies, detailed guides,
configuration templates, and metadata. Each component's visible `config.json` is editable.

Every component includes portable Python and its installed dependencies in
`.hookah/environment`. The Manager has an included SNMP client backend and
plotting libraries. No external Python, pip, or Net-SNMP installation is needed.
Windows launchers are native x64 console executables; Linux launchers are shell
commands for x86-64 glibc Linux (glibc 2.28+). `@SETUP@` is an optional offline check.

```text
@AGENT_COMMAND@ @VERSION_FLAG@
@MANAGER_COMMAND@ @DOCTOR@
```

The other operating system is a separate download. Releases contain exactly
`Hookah-Linux-@VERSION@.zip` and `Hookah-Windows-@VERSION@.zip`; there is no mixed-OS archive.

Developer source and tests are under `.hookah/source/`. Release manifests,
checksums, and actual validation results are under `.hookah/metadata/`.
