#!/usr/bin/env python3
"""Build the standalone Agent installation ZIP for Linux and Windows."""

import argparse
import sys
from pathlib import Path

from build_release import AGENT_FILES, build_component
from hookah_version import __version__


ROOT = Path(__file__).resolve().parent
PACKAGE_FILES = AGENT_FILES


def build_package(output, source=ROOT, platform="Windows"):
    return build_component("Agent", output, source, platform)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("Linux", "Windows"), default="Windows")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.output or ROOT / "dist" / f"Hookah-Agent-{args.platform}-{__version__}.zip"
        print(build_package(output, platform=args.platform))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
