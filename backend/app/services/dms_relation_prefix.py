"""Derive ``customer_master.dms_relation_prefix`` for DMS relation line (S/O style)."""


def compute_dms_relation_prefix(
    care_of: str | None = None,
    address: str | None = None,
    gender: str | None = None,
) -> str:
    """
    First three characters of trimmed ``care_of`` when length >= 3; else address; else gender fallback.
    """
    for source in (care_of, address):
        s = (source or "").strip()
        if len(s) >= 3:
            return s[:3]
    g = (gender or "").strip().lower()
    return "D/o" if g in ("f", "female") else "S/o"
