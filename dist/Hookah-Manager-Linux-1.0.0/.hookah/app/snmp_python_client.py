"""Portable SNMPv1/v2c client backend bundled with the Manager distributions."""

import argparse
import asyncio
import json
from pathlib import Path


def parse_target(value):
    value = value[4:] if value.startswith("udp:") else value
    host, separator, port = value.rpartition(":")
    if not separator:
        host, port = value, "161"
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Use an IPv4/hostname target, optionally udp:host:port.")
    return host, int(port)


def format_binding(oid, value):
    from pysnmp.proto import rfc1902, rfc1905

    name = "." + ".".join(map(str, oid))
    if isinstance(value, rfc1905.EndOfMibView):
        return None
    if isinstance(value, (rfc1905.NoSuchObject, rfc1905.NoSuchInstance)):
        return name + " = No Such Instance currently exists at this OID"
    if isinstance(value, rfc1902.ObjectIdentifier):
        return name + " = OID: ." + ".".join(map(str, value))
    if isinstance(value, rfc1902.IpAddress):
        return name + " = IpAddress: " + value.prettyPrint()
    if isinstance(value, rfc1902.OctetString):
        data = value.asOctets()
        try:
            text = data.decode("utf-8")
            if "\x00" in text:
                raise UnicodeError("binary octet string")
            return name + " = STRING: " + json.dumps(text, ensure_ascii=False)
        except UnicodeError:
            return name + " = Hex-STRING: " + data.hex(" ").upper()
    if isinstance(value, rfc1902.TimeTicks):
        return f"{name} = Timeticks: ({int(value)})"
    for kind, label in ((rfc1902.Counter64, "Counter64"), (rfc1902.Counter32, "Counter32"),
                        (rfc1902.Gauge32, "Gauge32"), (rfc1902.Integer32, "INTEGER")):
        if isinstance(value, kind):
            return f"{name} = {label}: {int(value)}"
    return name + " = " + value.prettyPrint()


def set_value(kind, value):
    from pysnmp.proto import rfc1902

    types = {"i": rfc1902.Integer32, "u": rfc1902.Gauge32, "t": rfc1902.TimeTicks,
             "c": rfc1902.Counter32, "U": rfc1902.Counter64}
    if kind in types:
        return types[kind](int(value))
    if kind == "s":
        return rfc1902.OctetString(value.encode("utf-8"))
    if kind == "x":
        return rfc1902.OctetString(bytes.fromhex(value))
    if kind == "d":
        return rfc1902.OctetString(bytes(int(part) for part in value.replace(",", " ").split()))
    if kind == "a":
        return rfc1902.IpAddress(value)
    if kind == "o":
        return rfc1902.ObjectIdentifier(tuple(int(part) for part in value.lstrip(".").split(".")))
    raise ValueError("Unsupported SET type. Choose i, u, t, c, U, s, x, d, a, or o.")


async def request(operation, args):
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine,
        UdpTransportTarget, bulk_cmd, get_cmd, next_cmd, set_cmd,
    )
    from pysnmp.proto import rfc1905

    client = SnmpEngine()
    try:
        target = await UdpTransportTarget.create(parse_target(args.target), timeout=args.timeout, retries=args.retries)
        auth = CommunityData(args.community, mpModel=0 if args.version == "1" else 1)
        identity = tuple(int(part) for part in args.oid.lstrip(".").split("."))
        binding = ObjectType(ObjectIdentity(identity))
        if operation == "snmpset":
            binding = ObjectType(ObjectIdentity(identity), set_value(args.values[0], args.values[1]))
        context = ContextData()
        if operation != "snmpwalk":
            action = set_cmd if operation == "snmpset" else get_cmd
            indication, status, _, bindings = await action(client, auth, target, context, binding, lookupMib=False)
            if indication or status:
                raise RuntimeError(str(indication) if indication else status.prettyPrint())
            return "\n".join(line for oid, value in bindings if (line := format_binding(oid, value)) is not None)
        rows, previous = [], identity
        while True:
            binding = ObjectType(ObjectIdentity(previous))
            if args.version == "1":
                response = await next_cmd(client, auth, target, context, binding, lookupMib=False)
            else:
                response = await bulk_cmd(client, auth, target, context, 0, 25, binding, lookupMib=False)
            indication, status, _, bindings = response
            if indication:
                raise RuntimeError(str(indication))
            if status:
                if args.version == "1" and str(status) == "noSuchName":
                    break
                raise RuntimeError(status.prettyPrint())
            if not bindings:
                break
            for oid, value in bindings:
                number = tuple(oid)
                if isinstance(value, (rfc1905.EndOfMibView, rfc1905.NoSuchObject, rfc1905.NoSuchInstance)) or number[:len(identity)] != identity:
                    return "\n".join(rows)
                if number <= previous:
                    raise RuntimeError("The device returned a non-increasing OID during WALK.")
                rows.append(format_binding(oid, value))
                previous = number
                if len(rows) >= 100000:
                    raise RuntimeError("WALK exceeded the maximum number of returned instances.")
        return "\n".join(rows)
    finally:
        client.close_dispatcher()


def run_client(arguments):
    operation = Path(arguments[0]).name.lower().removesuffix(".exe")
    if operation not in ("snmpget", "snmpwalk", "snmpset"):
        raise RuntimeError("Unsupported bundled SNMP operation: " + operation)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-v", dest="version", choices=("1", "2c"), required=True)
    parser.add_argument("-c", dest="community", required=True)
    parser.add_argument("-On", action="store_true")
    parser.add_argument("-Oa", action="store_true")
    parser.add_argument("-t", dest="timeout", type=float, default=2)
    parser.add_argument("-r", dest="retries", type=int, default=1)
    parser.add_argument("target")
    parser.add_argument("oid")
    parser.add_argument("values", nargs="*")
    if operation == "snmpset":
        if len(arguments) < 4:
            raise RuntimeError("SET requires a type and a value.")
        args = parser.parse_args(arguments[1:-2])
        args.values = list(arguments[-2:])
    else:
        args = parser.parse_args(arguments[1:])
    try:
        return asyncio.run(asyncio.wait_for(request(operation, args), max(15, args.timeout * (args.retries + 1) + 5)))
    except (TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("SNMP request failed: " + str(exc)) from exc
