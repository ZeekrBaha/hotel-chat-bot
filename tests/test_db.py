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


def test_enqueue_message_returns_true_for_new_message():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = True  # RPC: newly inserted
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.enqueue_message("wamid.new1", "whatsapp", "79991234567", "hi")
    assert result is True
    mock_client.rpc.assert_called_once_with("enqueue_message", {
        "p_message_id": "wamid.new1",
        "p_platform": "whatsapp",
        "p_sender_id": "79991234567",
        "p_text": "hi",
    })


def test_enqueue_message_returns_false_for_duplicate():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = False  # RPC: ON CONFLICT hit
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.enqueue_message("wamid.dup1", "whatsapp", "79991234567", "hi")
    assert result is False


def test_claim_message_job_returns_first_row():
    job = {"message_id": "wamid.1", "status": "processing", "text": "hi"}
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = [job]
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.claim_message_job()
    assert result == job


def test_claim_message_job_returns_none_when_empty():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = []
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.claim_message_job()
    assert result is None


def test_fail_message_job_returns_new_status():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = "dead"
    with patch("core.db.get_client", return_value=mock_client):
        status = db_module.fail_message_job("wamid.1", "boom")
    assert status == "dead"
    mock_client.rpc.assert_called_once_with("fail_message_job", {
        "p_message_id": "wamid.1", "p_error": "boom",
    })


def test_mark_reply_sent_calls_rpc():
    mock_client = MagicMock()
    with patch("core.db.get_client", return_value=mock_client):
        db_module.mark_reply_sent("wamid.1")
    mock_client.rpc.assert_called_once_with("mark_reply_sent", {"p_message_id": "wamid.1"})


def test_record_message_event_swallows_errors():
    mock_client = MagicMock()
    mock_client.rpc.side_effect = Exception("db down")
    with patch("core.db.get_client", return_value=mock_client):
        # Must not raise: auditing failures cannot break message processing.
        db_module.record_message_event("wamid.1", "inbound", {"x": 1})


def test_check_health_runs_a_real_query():
    mock_client = MagicMock()
    with patch("core.db.get_client", return_value=mock_client):
        db_module.check_health()
    mock_client.table.assert_called_once_with("conversations")
    mock_client.table.return_value.select.return_value.limit.return_value.execute.assert_called_once()


_BOOKING = {
    "guest_name": "Айгуль",
    "check_in": "2026-06-05",
    "check_out": "2026-06-07",
    "num_guests": 2,
}


def test_claim_booking_alert_returns_true_when_claimed():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = True
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.claim_booking_alert("whatsapp", "79991234567", _BOOKING)
    assert result is True
    mock_client.rpc.assert_called_once_with("claim_booking_alert", {
        "p_platform": "whatsapp",
        "p_sender_id": "79991234567",
        "p_key": "Айгуль|2026-06-05|2026-06-07|2",
    })


def test_claim_booking_alert_returns_false_when_not_claimed():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = False
    with patch("core.db.get_client", return_value=mock_client):
        result = db_module.claim_booking_alert("whatsapp", "79991234567", _BOOKING)
    assert result is False


def test_finish_booking_alert_passes_success_flag():
    mock_client = MagicMock()
    with patch("core.db.get_client", return_value=mock_client):
        db_module.finish_booking_alert("whatsapp", "79991234567", _BOOKING, False)
    mock_client.rpc.assert_called_once_with("finish_booking_alert", {
        "p_platform": "whatsapp",
        "p_sender_id": "79991234567",
        "p_key": "Айгуль|2026-06-05|2026-06-07|2",
        "p_success": False,
    })


def test_mark_history_appended_calls_rpc():
    mock_client = MagicMock()
    with patch("core.db.get_client", return_value=mock_client):
        db_module.mark_history_appended("wamid.1")
    mock_client.rpc.assert_called_once_with("mark_history_appended", {"p_message_id": "wamid.1"})
