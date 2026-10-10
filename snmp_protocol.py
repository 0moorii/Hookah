"""Shared wire-level collection settings; no agent dependencies are required."""

# Documentation/example enterprise number (RFC 5612).
EXAMPLE_ROOT = (1, 3, 6, 1, 4, 1, 32473, 1)
COLLECTION_CONFIG_OID = EXAMPLE_ROOT + (3, 1, 0)
COLLECTION_TABLE = EXAMPLE_ROOT + (3, 2)
COLLECTION_GROUPS = {
    "system": 1,
    "interfaces": 2,
    "ifInOctets": 3,
    "ifOutOctets": 4,
    "cpu": 5,
    "memory": 6,
    "load": 7,
    "storage": 8,
    "diskio": 9,
    "processes": 10,
    "connections": 11,
    "sensors": 12,
    "services": 13,
}


def oid_text(oid):
    return "." + ".".join(map(str, oid))


def collection_group_for_oid(oid):
    """Identify the sampling group of a standard or lab-agent metric OID."""
    from snmp_mib import group_for_oid
    registered = group_for_oid(oid)
    if registered is not None:
        return registered
    value = str(oid).strip().lstrip(".")
    try:
        number = tuple(int(part) for part in value.split("."))
    except ValueError:
        return None
    prefixes = (
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 10), "ifInOctets"),
        ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 6), "ifInOctets"),
        ((1, 3, 6, 1, 2, 1, 2, 2, 1, 16), "ifOutOctets"),
        ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 10), "ifOutOctets"),
        ((1, 3, 6, 1, 2, 1, 25, 3, 3, 1, 2), "cpu"),
        ((1, 3, 6, 1, 4, 1, 2021, 11, 11), "cpu"),
        ((1, 3, 6, 1, 2, 1, 25, 2), "memory"),
        ((1, 3, 6, 1, 4, 1, 2021, 4), "memory"),
        ((1, 3, 6, 1, 4, 1, 2021, 10), "load"),
        ((1, 3, 6, 1, 2, 1, 2), "interfaces"),
        ((1, 3, 6, 1, 2, 1, 31), "interfaces"),
        (EXAMPLE_ROOT + (2,), "interfaces"),
        ((1, 3, 6, 1, 2, 1, 1), "system"),
        ((1, 3, 6, 1, 2, 1, 25, 1), "system"),
    )
    for prefix, group in prefixes:
        if number[:len(prefix)] == prefix:
            return group
    return None


def collection_metadata_oid(group, column):
    """Columns: 1=name, 2=interval seconds, 3=sample seconds, 4=sample count."""
    if column not in (1, 2, 3, 4):
        raise ValueError("Collection metadata column must be 1, 2, 3, or 4.")
    return COLLECTION_TABLE + (column, COLLECTION_GROUPS[group])
