"""Bridge Playwright RTO fill (worker thread) with operator OTP entry via HTTP API."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Final

_lock = threading.Lock()
# (dealer_id, rto_queue_id) -> (event, otp_cell)
_slots: dict[tuple[int, int], tuple[threading.Event, list[str | None]]] = {}


class OperatorOtpTimeout(TimeoutError):
    """Raised when the operator does not submit an OTP before the deadline."""


_OTP_MAX_WAIT_S: Final[float] = 600.0


def wait_for_operator_otp(
    dealer_id: int,
    rto_queue_id: int,
    notify: Callable[[], None],
    *,
    timeout_s: float = _OTP_MAX_WAIT_S,
) -> str:
    """Block until ``deliver_operator_otp`` is called for the same key, then return digits/normalized OTP.

    ``notify`` runs after the wait slot is registered (safe to publish status for the UI).
    Caller should clear ``otp_pending`` in batch status after filling Vahan or on timeout.
    """
    key = (int(dealer_id), int(rto_queue_id))
    ev = threading.Event()
    cell: list[str | None] = [None]
    with _lock:
        if key in _slots:
            raise RuntimeError(f"Duplicate OTP wait for dealer={dealer_id} rto_queue_id={rto_queue_id}")
        _slots[key] = (ev, cell)
    try:
        notify()
        if not ev.wait(timeout=timeout_s):
            raise OperatorOtpTimeout(f"No OTP submitted within {timeout_s:.0f}s")
        raw = cell[0]
        if not raw or not str(raw).strip():
            raise RuntimeError("Empty OTP submission")
        s = str(raw).strip()
        digits = re.sub(r"\D", "", s)
        return digits if digits else s
    finally:
        with _lock:
            _slots.pop(key, None)


def deliver_operator_otp(dealer_id: int, rto_queue_id: int, otp: str) -> bool:
    """Record OTP and unblock ``wait_for_operator_otp``. Returns False if no waiter exists."""
    key = (int(dealer_id), int(rto_queue_id))
    with _lock:
        slot = _slots.get(key)
        if not slot:
            return False
        ev, cell = slot
        cell[0] = otp
        ev.set()
        return True


def has_pending_wait(dealer_id: int, rto_queue_id: int) -> bool:
    with _lock:
        return (int(dealer_id), int(rto_queue_id)) in _slots
