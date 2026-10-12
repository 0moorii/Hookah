# Bundled third-party software

Hookah's portable distributions include Python 3.12.15 from the
`astral-sh/python-build-standalone` 20261009 release. The exact download URL and
SHA-256 are recorded in `.hookah/metadata/BUNDLE.json` and the build lock file.
Python's license is retained in the environment's `LICENSE.txt` (Windows) or
`lib/python3.12/LICENSE.txt` (Linux).

The Agent includes PySNMP, psutil, cryptography, pyasn1, cffi, and pycparser.
Windows Agent bundles also include pywin32. The Manager includes PySNMP,
cryptography, Matplotlib, NumPy, Pillow, contourpy, cycler, fonttools, kiwisolver,
packaging, pyparsing, python-dateutil, six, and their supporting modules.
Installed versions are listed in `.hookah/metadata/BUNDLE.json`.

Each distribution retains its wheel-provided metadata and licenses in
`.hookah/environment/Lib/site-packages/` on Windows or
`.hookah/environment/lib/python3.12/site-packages/` on Linux. Look for each
package's `.dist-info/licenses/`, `LICENSE`, or `COPYING` files. Public CA
certificate bundles are runtime dependencies, not private Hookah keys.

Linux binaries target x86-64 glibc systems with glibc 2.28 or newer. Windows
binaries target Windows 10/11 x64. System libraries and operating-system drivers
remain provided by the OS; GUI plot windows require a usable display environment.
