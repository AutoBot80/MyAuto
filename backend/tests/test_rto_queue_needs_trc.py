"""RTO queue insert — Needs TRC when customer and dealer states differ."""

from unittest.mock import MagicMock, patch

from app.repositories import rto_payment_details as repo
from app.services.customer_address_infer import canonical_states_differ


def test_canonical_states_differ_true_for_known_mismatch() -> None:
    assert canonical_states_differ("Uttar Pradesh", "RAJASTHAN") is True
    assert canonical_states_differ("UP", "RJ") is True


def test_canonical_states_differ_false_same_or_invalid() -> None:
    assert canonical_states_differ("Rajasthan", "RAJASTHAN") is False
    assert canonical_states_differ("xyz", "RAJASTHAN") is False
    assert canonical_states_differ("", "RAJASTHAN") is False
    assert canonical_states_differ("Uttar Pradesh", "") is False


def test_indian_state_two_letter_code_for_siebel() -> None:
    from app.services.customer_address_infer import indian_state_two_letter_code

    assert indian_state_two_letter_code("UTTAR PRADESH") == "UP"
    assert indian_state_two_letter_code("Uttar Pradesh") == "UP"
    assert indian_state_two_letter_code("Rajasthan") == "RJ"
    assert indian_state_two_letter_code("Odisha") == "OD"
    assert indian_state_two_letter_code("xyz") is None


@patch("app.repositories.rto_payment_details.get_connection")
@patch("app.repositories.rto_payment_details.get_customer_and_dealer_states_for_sales")
def test_insert_interstate_sets_needs_trc_and_in_queue_false(
    mock_states: MagicMock,
    mock_get_conn: MagicMock,
) -> None:
    mock_states.return_value = ("Uttar Pradesh", "Rajasthan")
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"rto_queue_id": 42}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_conn.return_value = mock_conn

    qid = repo.insert(sales_id=100, dealer_id=100001, status="Queued")
    assert qid == 42
    args = mock_cur.execute.call_args[0][1]
    # sales_id, staging, dealer, insurance, mobile, app_date, amount, status, in_queue
    assert args[7] == repo.RTO_STATUS_NEEDS_TRC
    assert args[8] is False
    sql = mock_cur.execute.call_args[0][0]
    assert "in_queue" in sql


@patch("app.repositories.rto_payment_details.get_connection")
@patch("app.repositories.rto_payment_details.get_customer_and_dealer_states_for_sales")
def test_insert_same_state_stays_queued_in_queue_true(
    mock_states: MagicMock,
    mock_get_conn: MagicMock,
) -> None:
    mock_states.return_value = ("Rajasthan", "RAJASTHAN")
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"rto_queue_id": 7}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_conn.return_value = mock_conn

    qid = repo.insert(sales_id=101, dealer_id=100001, status="Queued")
    assert qid == 7
    args = mock_cur.execute.call_args[0][1]
    assert args[7] == "Queued"
    assert args[8] is True


@patch("app.repositories.rto_payment_details.get_connection")
@patch("app.repositories.rto_payment_details.get_customer_and_dealer_states_for_sales")
def test_insert_invalid_customer_state_stays_queued(
    mock_states: MagicMock,
    mock_get_conn: MagicMock,
) -> None:
    mock_states.return_value = ("xyz", "Rajasthan")
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"rto_queue_id": 9}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_conn.return_value = mock_conn

    repo.insert(sales_id=102, dealer_id=100001, status="Queued")
    args = mock_cur.execute.call_args[0][1]
    assert args[7] == "Queued"
    assert args[8] is True


@patch("app.repositories.rto_payment_details.get_connection")
def test_mark_manually_completed_excludes_needs_trc(mock_get_conn: MagicMock) -> None:
    mock_cur = MagicMock()
    mock_cur.rowcount = 0
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_conn.return_value = mock_conn

    assert repo.mark_manually_completed(55) is False
    sql = mock_cur.execute.call_args[0][0]
    assert "Needs TRC" not in sql
    assert "'Queued'" in sql


@patch("app.repositories.rto_payment_details.get_connection")
def test_set_in_queue_excludes_needs_trc(mock_get_conn: MagicMock) -> None:
    mock_cur = MagicMock()
    mock_cur.rowcount = 0
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_conn.return_value = mock_conn

    assert repo.set_in_queue(55, True) is False
    status_list = mock_cur.execute.call_args[0][1][2]
    assert "Needs TRC" not in status_list
    assert "Queued" in status_list
