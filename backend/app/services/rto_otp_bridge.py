"""Bridge Playwright RTO fill (worker thread) with operator OTP / mobile-change via HTTP API."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

_lock = threading.Lock()
# (dealer_id, rto_queue_id) -> (event, action_cell)
_slots: dict[tuple[int, int], tuple[threading.Event, list["OperatorAction | None"]]] = {}


class OperatorOtpTimeout(TimeoutError):
    """Raised when the operator does not submit an action before the deadline."""


@dataclass(frozen=True)
class OperatorAction:
    kind: Literal["otp", "change_mobile"]
    value: str


_OTP_MAX_WAIT_S: Final[float] = 600.0


def wait_for_operator_action(
    dealer_id: int,
    rto_queue_id: int,
    notify: Callable[[], None],
    *,
    timeout_s: float = _OTP_MAX_WAIT_S,
) -> OperatorAction:
    """Block until ``deliver_operator_otp`` or ``deliver_operator_change_mobile`` unblocks the wait.

    ``notify`` runs after the wait slot is registered (safe to publish status for the UI).
    Caller should clear ``otp_pending`` in batch status after the Vahan flow completes or on timeout.
    """
    key = (int(dealer_id), int(rto_queue_id))
    ev = threading.Event()
    cell: list[OperatorAction | None] = [None]
    with _lock:
        if key in _slots:
            raise RuntimeError(f"Duplicate operator wait for dealer={dealer_id} rto_queue_id={rto_queue_id}")
        _slots[key] = (ev, cell)
    try:
        notify()
        if not ev.wait(timeout=timeout_s):
            raise OperatorOtpTimeout(f"No OTP or mobile change within {timeout_s:.0f}s")
        raw = cell[0]
        if raw is None:
            raise RuntimeError("Empty operator submission")
        return raw
    finally:
        with _lock:
            _slots.pop(key, None)


def deliver_operator_action(dealer_id: int, rto_queue_id: int, action: OperatorAction) -> bool:
    """Unblock ``wait_for_operator_action``. Returns False if no waiter exists."""
    key = (int(dealer_id), int(rto_queue_id))
    with _lock:
        slot = _slots.get(key)
        if not slot:
            return False
        ev, cell = slot
        cell[0] = action
        ev.set()
        return True


def deliver_operator_otp(dealer_id: int, rto_queue_id: int, otp: str) -> bool:
    """Record OTP and unblock the wait."""
    s = str(otp).strip()
    if not s:
        return False
    digits = re.sub(r"\D", "", s)
    val = digits if digits else s
    return deliver_operator_action(dealer_id, rto_queue_id, OperatorAction("otp", val))


def deliver_operator_change_mobile(dealer_id: int, rto_queue_id: int, mobile: str) -> bool:
    """Request mobile change: automation will cancel OTP dialog, update form, Partial Save again."""
    d = re.sub(r"\D", "", str(mobile).strip())
    if len(d) < 10:
        return False
    d10 = d[-10:]
    if not re.match(r"^[6-9]\d{9}$", d10):
        return False
    return deliver_operator_action(dealer_id, rto_queue_id, OperatorAction("change_mobile", d10))


def has_pending_wait(dealer_id: int, rto_queue_id: int) -> bool:
    with _lock:
        return (int(dealer_id), int(rto_queue_id)) in _slots
