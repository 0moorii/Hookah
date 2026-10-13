# Running Hookah on @PLATFORM@

1. Download `Hookah-@PLATFORM@-@VERSION@.zip` for this operating system.
2. Choose `Agent` for the monitored device or `Manager` for the monitoring host.
3. Run the launcher directly; Python and dependencies are already included.
4. Edit its `config.json` for your network, then run the launcher normally.

@PLATFORM@ commands from the extracted release folder:

```text
@AGENT_COMMAND@ @SETUP@
@AGENT_COMMAND@ @VERSION_FLAG@
@MANAGER_COMMAND@ @SETUP@
@MANAGER_COMMAND@ @DOCTOR@
@MANAGER_COMMAND@ --help
```

Each component's `HELP.md` describes its commands. Detailed guides and service
scripts live inside its `.hookah/` support folder. Implementation files are
grouped under `.hookah/app/`, and `.hookah/environment/` contains the ready-to-use
portable runtime. `@SETUP@` only verifies/configures the package, entirely offline.

This archive does not contain packages for the other operating system. Linux
commands use parameters only; the Windows Manager retains its terminal menu.

For testing or rebuilding, use the complete developer project in
`.hookah/source/`; its `TESTING.md` explains the unit, integration, smoke, and
platform-launcher verification commands.
