import pytest
from unittest.mock import patch
from core.notify import send_owner_alert


def _mock_post_setup(mock_post, json_body=None):
    """Configure mock post to return no HTTP error and optional JSON body."""
    mock_post.return_value.raise_for_status.return_value = None
    mock_post.return_value.json.return_value = json_body or {}


def test_send_owner_alert_posts_to_correct_url():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert(
            sender_id="79991234567",
            platform="whatsapp",
            message_text="Хочу забронировать",
            bot_reply="Спасибо! Уточните даты.",
        )
    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    assert "123456789" in url   # WHATSAPP_PHONE_NUMBER_ID from conftest


def test_send_owner_alert_includes_bearer_token():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"


def test_send_owner_alert_message_body_contains_key_info():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert(
            sender_id="79991234567",
            platform="whatsapp",
            message_text="Хочу забронировать",
            bot_reply="Уточните имя и даты.",
        )
    body = mock_post.call_args.kwargs["json"]["text"]["body"]
    assert "79991234567" in body
    assert "whatsapp" in body
    assert "Хочу забронировать" in body
    assert "Уточните имя и даты." in body


def test_send_owner_alert_sends_to_owner_number():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["to"] == "79991234567"   # OWNER_PHONE_NUMBER from conftest


def test_send_owner_alert_uses_timeout():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    _, kwargs = mock_post.call_args
    assert kwargs.get("timeout") == (3, 10)


def test_send_owner_alert_raises_on_meta_api_error_in_body():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post, json_body={
            "error": {"code": 131047, "message": "Message failed to send"}
        })
        with pytest.raises(RuntimeError, match="Meta API error"):
            send_owner_alert("79991234567", "whatsapp", "msg", "reply")
