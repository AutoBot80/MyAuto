from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """Authenticated user context (from JWT or dev bypass)."""

    login_id: str
    dealer_id: int
    name: str | None
    roles: tuple[str, ...] = field(default_factory=tuple)
    admin: bool = False
