from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """Authenticated user context (from JWT or dev bypass)."""

    login_id: str
    dealer_id: int
    name: str | None
    roles: tuple[str, ...] = field(default_factory=tuple)
    admin: bool = False
    # Home tiles: OR of roles_ref.*_flag for this login at this dealer (JWT + /auth/me).
    tile_pos: bool = False
    tile_rto: bool = False
    tile_service: bool = False
    tile_dealer: bool = False
