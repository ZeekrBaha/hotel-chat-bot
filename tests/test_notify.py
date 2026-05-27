import pytest
from unittest.mock import patch
from core.notify import send_owner_alert

BOOKING = {
    "guest_name": "Айгуль",
    "check_in": "05.06.2026",
    "check_out": "07.06.2026",
    "num_guests": 2,
}


def _mock_post_setup(mock_post, json_body=None):
    mock_post.return_value.raise_for_status.return_value = None
    mock_post.return_value.json.return_value = json_body or {}


def test_send_owner_alert_posts_to_correct_url():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    url = mock_post.call_args.args[0]
    assert "123456789" in url   # WHATSAPP_PHONE_NUMBER_ID from conftest


def test_send_owner_alert_includes_bearer_token():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"


def test_send_owner_alert_formats_structured_booking():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    body = mock_post.call_args.kwargs["json"]["text"]["body"]
    assert "Айгуль" in body
    assert "05.06.2026" in body
    assert "07.06.2026" in body
    assert "2" in body
    assert "79991234567" in body


def test_send_owner_alert_sends_to_owner_number():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    assert mock_post.call_args.kwargs["json"]["to"] == "79991234567"   # OWNER_PHONE_NUMBER


def test_send_owner_alert_uses_timeout():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    assert mock_post.call_args.kwargs["timeout"] == (3, 10)


def test_send_owner_alert_raises_on_meta_api_error_in_body():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post, json_body={
            "error": {"code": 131047, "message": "Message failed to send"}
        })
        with pytest.raises(RuntimeError, match="Meta API error"):
            send_owner_alert("79991234567", "whatsapp", BOOKING)
