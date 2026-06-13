from fastapi import Depends, HTTPException, Request

from app.security.principal import Principal


def get_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return principal


def assert_can_access_dealer(principal: Principal, target_dealer_id: int) -> None:
    """Operators may only access their dealer; admins use ``admin_dealer_access_ref`` scope."""
    if principal.admin:
        from app.repositories.admin_dealer_access import assert_dealer_in_admin_scope

        assert_dealer_in_admin_scope(principal.login_id, int(target_dealer_id))
        return
    if int(target_dealer_id) == int(principal.dealer_id):
        return
    raise HTTPException(status_code=403, detail="Access denied for this dealer")


def resolve_dealer_id(principal: Principal, dealer_id: int | None) -> int:
    """Use query/form dealer_id or default to token dealer_id; forbid cross-tenant for non-admins."""
    if principal.admin:
        from app.repositories.admin_dealer_access import resolve_admin_scoped_dealer_id

        return resolve_admin_scoped_dealer_id(principal.login_id, int(principal.dealer_id), dealer_id)
    did = int(dealer_id) if dealer_id is not None else int(principal.dealer_id)
    assert_can_access_dealer(principal, did)
    return did
