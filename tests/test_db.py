from unittest.mock import MagicMock, patch
from core.db import get_history, save_history


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


def test_save_history_calls_upsert_with_correct_payload():
    mock_client = MagicMock()
    messages = [{"role": "user", "content": "Привет"}]
    with patch("core.db.get_client", return_value=mock_client):
        save_history("whatsapp", "79991234567", messages)
    mock_client.table.return_value.upsert.assert_called_once_with(
        {"platform": "whatsapp", "sender_id": "79991234567", "messages": messages},
        on_conflict="platform,sender_id",
    )


def test_save_history_trims_to_last_20_messages():
    mock_client = MagicMock()
    messages = [{"role": "user", "content": str(i)} for i in range(25)]
    with patch("core.db.get_client", return_value=mock_client):
        save_history("whatsapp", "79991234567", messages)
    saved = mock_client.table.return_value.upsert.call_args[0][0]["messages"]
    assert len(saved) == 20
    assert saved[0]["content"] == "5"   # oldest 5 dropped
    assert saved[-1]["content"] == "24"


def test_get_client_returns_singleton():
    import core.db as db_module
    db_module._supabase_client = None
    with patch("core.db.create_client") as mock_create:
        mock_create.return_value = MagicMock()
        client1 = db_module.get_client()
        client2 = db_module.get_client()
    assert mock_create.call_count == 1
    assert client1 is client2
    db_module._supabase_client = None
