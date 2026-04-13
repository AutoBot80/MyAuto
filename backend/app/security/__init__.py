"""JWT auth, principals, and route dependencies."""

from app.security.principal import Principal
from app.security.deps import (
    assert_can_access_dealer,
    get_principal,
    require_admin,
    resolve_dealer_id,
)

__all__ = [
    "Principal",
    "get_principal",
    "require_admin",
    "resolve_dealer_id",
    "assert_can_access_dealer",
]
