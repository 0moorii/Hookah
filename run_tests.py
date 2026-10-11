#!/usr/bin/env python3
"""Run isolated Agent/Manager unit tests and real loopback SNMP integration tests."""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from hookah_version import __version__


ROOT = Path(__file__).resolve().parent


def test_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_cases(item)
        else:
            yield item


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "unit", "integration"), default="all")
    parser.add_argument("--report", type=Path, help="Write machine-readable results for release verification")
    args = parser.parse_args(argv)
    missing = [name for name in ("psutil", "pysnmp", "matplotlib", "cryptography") if importlib.util.find_spec(name) is None]
    if missing:
        parser.error("Missing test dependencies: " + ", ".join(missing) + ". Run make setup-test first.")
    if args.suite != "unit":
        tools = ("snmpget", "snmpgetnext", "snmpwalk", "snmpbulkwalk", "snmpset")
        missing = [name for name in tools if shutil.which(name) is None]
        if missing:
            parser.error("Missing Net-SNMP clients: " + ", ".join(missing) + ". On Debian/Ubuntu: sudo apt install snmp")
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix="snmp-test-matplotlib-") as cache:
        os.environ["MPLCONFIGDIR"] = cache
        discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
        selected = unittest.TestSuite(
            test for test in test_cases(discovered)
            if args.suite == "all"
            or bool(getattr(test, "integration_test", False)) == (args.suite == "integration")
        )
        if selected.countTestCases() == 0:
            parser.error("No tests were discovered for the selected suite.")
        result = unittest.TextTestRunner(verbosity=2, buffer=True).run(selected)
    if args.report:
        report = {
            "version": __version__, "suite": args.suite,
            "status": "passed" if result.wasSuccessful() else "failed",
            "tests_run": result.testsRun,
            "failures": [str(test) for test, _ in result.failures],
            "errors": [str(test) for test, _ in result.errors],
            "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
            "platform": sys.platform, "python": sys.version.split()[0],
            "windows_launcher_runner": "native" if os.name == "nt" else
                "wine" if os.environ.get("HOOKAH_WINE") or shutil.which("wine") or shutil.which("wine64") else "unavailable",
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
