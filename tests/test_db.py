from unittest.mock import MagicMock, patch
from core.db import get_history
import core.db as db_module


def _make_mock_client(data=None):
    """Return a mock supabase client pre-configured for the chained query API."""
    mock = MagicMock()
    mock.table.return_value \
        .select.return_value \
        .eq.return_value \
        .eq.return_value \
        .execute.return_value \
        .data = data or []
    return mock


def test_get_history_returns_empty_list_when_no_record():
    mock_client = _make_mock_client(data=[])
    with patch("core.db.get_client", return_value=mock_client):
        result = get_history("whatsapp", "79991234567")
    assert result == []


def test_get_history_returns_messages_when_record_exists():
    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Добрый день!"},
    ]
    mock_client = _make_mock_client(data=[{"messages": messages}])
    with patch("core.db.get_client", return_value=mock_client):
        result = get_history("whatsapp", "79991234567")
    assert result == messages


def test_increment_daily_counter_calls_rpc_with_correct_params():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = 5
    with patch("core.db.get_client", return_value=mock_client):
        count = db_module.increment_daily_counter("whatsapp", "79991234567")
    mock_client.rpc.assert_called_once_with("increment_daily_counter", {
        "p_platform": "whatsapp", "p_sender_id": "79991234567",
    })
    assert count == 5


def test_increment_daily_counter_returns_1_as_fallback_for_null_result():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = None
    with patch("core.db.get_client", return_value=mock_client):
        count = db_module.increment_daily_counter("whatsapp", "79991234567")
    assert count == 1


def test_get_client_returns_singleton():
    db_module._supabase_client = None
    with patch("core.db.create_client") as mock_create:
        mock_create.return_value = MagicMock()
        client1 = db_module.get_client()
        client2 = db_module.get_client()
    assert mock_create.call_count == 1
    assert client1 is client2
    db_module._supabase_client = None


def test_is_duplicate_message_returns_false_for_new_message():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = True  # RPC: newly inserted
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.is_duplicate_message("wamid.new1")
    assert result is False


def test_is_duplicate_message_returns_true_for_seen_message():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = False  # RPC: ON CONFLICT hit
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.is_duplicate_message("wamid.dup1")
    assert result is True


def test_check_and_set_booking_alert_returns_true_for_new_booking():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = True  # RPC: row updated
    booking = {
        "guest_name": "Айгуль",
        "check_in": "2026-06-05",
        "check_out": "2026-06-07",
        "num_guests": 2,
    }
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.check_and_set_booking_alert("whatsapp", "79991234567", booking)
    assert result is True
    mock_client.rpc.assert_called_once_with("set_booking_alert_if_new", {
        "p_platform": "whatsapp",
        "p_sender_id": "79991234567",
        "p_key": "Айгуль|2026-06-05|2026-06-07|2",
    })


def test_check_and_set_booking_alert_returns_false_for_same_booking():
    booking = {
        "guest_name": "Айгуль",
        "check_in": "2026-06-05",
        "check_out": "2026-06-07",
        "num_guests": 2,
    }
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = False  # RPC: row not updated
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.check_and_set_booking_alert("whatsapp", "79991234567", booking)
    assert result is False
