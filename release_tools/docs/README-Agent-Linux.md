# Hookah Agent @VERSION@ — Linux

The Agent runs on the monitored device. Enter the release's `Agent/` folder;
its Linux command is **hookah-ag**.
All operations use parameters; there is no numbered menu.

```text
hookah-ag
README.md
HELP.md
config.json
.hookah/          portable Python, application, dependencies, and detailed guides
```

Keep the complete directory together. Python 3.12, PySNMP, psutil, and encryption
dependencies are included. No installation or internet connection is needed.
If your extractor discarded executable permissions, run `chmod +x hookah-ag`.

From this folder:

```bash
./hookah-ag -v
./hookah-ag --config-show
./hookah-ag --install-cli
export PATH="$HOME/.local/bin:$PATH"
hookah-ag -a 192.168.1.10
hookah-ag --start
```

`--install-cli` creates a user-local symlink; `--bin-dir PATH` chooses another
directory. It never replaces an unrelated command. Without installation, use
`./hookah-ag`. The installed command also works from another directory.

The initial profile listens on UDP 1161 and permits loopback only. `-a IP_OR_CIDR`
adds a permitted Manager to the existing allowlist. With no parameters, the Agent
starts in the foreground; `--start` is the explicit equivalent. Ctrl+C stops it.

```bash
hookah-ag --config-set --interval ifInOctets 10
hookah-ag --config-set --sample-interval 1
hookah-ag --mib --group memory --values
```

The visible `config.json` stores defaults. `--config PATH` chooses another profile.
Sampling changes reload live; restart after changing listening settings,
communities, or permitted Manager addresses. `--setup` verifies the bundled
runtime offline. `HOOKAH_PYTHON` is an explicit developer interpreter override.

Run `hookah-ag --help` or read `HELP.md`. The complete parameter reference,
including remote configuration and encryption, is `.hookah/docs/LINUX-CLI.md`.
Windows commands and menus are not changed by this Linux interface.
