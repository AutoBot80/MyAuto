"""Derive ``customer_master.dms_relation_prefix`` for DMS relation line (S/O style)."""


def compute_dms_relation_prefix(address: str | None, gender: str | None) -> str:
    """
    Base rule: first three characters of trimmed address when length >= 3.
    Fallback: ``D/o`` for female, ``S/o`` otherwise.
    """
    a = (address or "").strip()
    if len(a) >= 3:
        return a[:3]
    g = (gender or "").strip().lower()
    return "D/o" if g in ("f", "female") else "S/o"
