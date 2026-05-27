from unittest.mock import patch
from core.notify import send_owner_alert


def test_send_owner_alert_posts_to_correct_url():
    with patch("core.notify.requests.post") as mock_post:
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
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"


def test_send_owner_alert_message_body_contains_key_info():
    with patch("core.notify.requests.post") as mock_post:
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
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["to"] == "79991234567"   # OWNER_PHONE_NUMBER from conftest
