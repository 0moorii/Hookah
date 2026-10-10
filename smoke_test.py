#!/usr/bin/env python3
"""Run a short real Agent/Manager loopback smoke check before release packaging."""

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from hookah_version import __version__


def smoke_check():
    from tests.helpers import READ_COMMUNITY, WRITE_COMMUNITY, RunningAgent, run_executable

    checks = []

    def successful(result, name):
        if result.returncode != 0:
            raise RuntimeError(f"{name} failed: {result.stdout}{result.stderr}")
        checks.append(name)
        return result.stdout

    with RunningAgent(sample_interval=0.1) as agent:
        profile = agent.path / "manager.json"
        profile.write_text(json.dumps({"target": agent.target, "community": READ_COMMUNITY,
                                       "interval": 0.1, "timeout": 1, "retries": 0}), encoding="utf-8")

        def manager(*arguments):
            return run_executable("snmp_data_collector_v12.py", "--config", profile, *arguments, cwd=agent.path)

        description = successful(manager("get", "--oid", ".1.3.6.1.2.1.1.1.0"), "Manager GET using its JSON profile")
        if "Python SNMP host agent" not in description or "Made by 0moorii" in description:
            raise RuntimeError("GET did not return clean scriptable Agent data.")
        successful(manager("walk", "--oid", ".1.3.6.1.2.1.2.2.1.2"), "Manager interface WALK")
        successful(manager("get", "--oid", ".1.3.6.1.2.1.25.3.3.1.2"), "Aggregate CPU GET")
        successful(manager("get", "--oid", ".1.3.6.1.2.1.25.2.3"), "Aggregate physical-memory GET")
        successful(manager("set", "--community", WRITE_COMMUNITY, "--oid", ".1.3.6.1.2.1.1.6.0",
                           "--type", "s", "--value", "Release smoke test"), "Manager authorized SET")
        readback = successful(manager("get", "--oid", ".1.3.6.1.2.1.1.6.0"), "SET read-back")
        if "Release smoke test" not in readback:
            raise RuntimeError("SET read-back did not match the written value.")
        capture = agent.path / "cpu.csv"
        successful(manager("capture", "--oid", ".1.3.6.1.2.1.25.3.3.1.2", "--sync-agent",
                           "--duration", "0.45", "--output", capture), "Bounded synchronized CPU capture")
        with capture.open(newline="", encoding="utf-8") as source:
            if not list(csv.DictReader(source)):
                raise RuntimeError("Capture did not write any samples.")
        image = agent.path / "plots" / "cpu.png"
        successful(manager("plot", capture, "--output", image, "--no-show"), "Headless PNG plot")
        if image.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("The plot is not a PNG image.")
        successful(manager("compare", capture, capture, "--output", agent.path / "comparison.png"),
                   "Non-interactive comparison plot")
        shown = successful(run_executable("snmp_agent.py", "config", "show", "--remote", agent.target,
                                          "--community", READ_COMMUNITY, cwd=agent.path), "Agent remote configuration CLI")
        if json.loads(shown)["sample_interval"] != 0.1:
            raise RuntimeError("Remote sampling defaults did not match.")
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write release smoke-test results as JSON")
    args = parser.parse_args(argv)
    missing = [name for name in ("snmpget", "snmpwalk", "snmpset") if shutil.which(name) is None]
    if missing:
        parser.error("Missing Net-SNMP clients: " + ", ".join(missing))
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    report = {"version": __version__, "suite": "smoke", "platform": sys.platform,
              "python": sys.version.split()[0], "status": "failed", "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="hookah-smoke-matplotlib-") as cache:
            os.environ["MPLCONFIGDIR"] = cache
            report["checks"] = smoke_check()
        report["status"] = "passed"
        print(f"Smoke test passed: {len(report['checks'])} Agent/Manager checks.")
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        report["error"] = str(exc)
        print(f"Smoke test failed: {exc}", file=sys.stderr)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
