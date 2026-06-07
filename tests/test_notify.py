import pytest
from unittest.mock import patch
from core.notify import send_owner_alert, send_escalation_alert

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


def test_send_escalation_alert_notifies_owner():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_escalation_alert("79991234567", "whatsapp")
    mock_post.assert_called_once()
    body = mock_post.call_args.kwargs["json"]["text"]["body"]
    assert "79991234567" in body
    assert "whatsapp" in body


def test_send_owner_alert_raises_on_meta_api_error_in_body():
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post, json_body={
            "error": {"code": 131047, "message": "Message failed to send"}
        })
        with pytest.raises(RuntimeError, match="Meta API error"):
            send_owner_alert("79991234567", "whatsapp", BOOKING)


# --- WhatsApp template path (works outside Meta's 24h window) ---

def test_send_owner_alert_uses_template_when_configured(monkeypatch):
    monkeypatch.setenv("OWNER_ALERT_TEMPLATE", "owner_booking_alert")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_LANG", "ru")
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    body = mock_post.call_args.kwargs["json"]
    assert body["type"] == "template"
    assert body["template"]["name"] == "owner_booking_alert"
    assert body["template"]["language"]["code"] == "ru"
    params = [p["text"] for p in body["template"]["components"][0]["parameters"]]
    assert params == ["Айгуль", "05.06.2026", "07.06.2026", "2", "whatsapp", "79991234567"]


def test_send_escalation_alert_uses_template_when_configured(monkeypatch):
    monkeypatch.setenv("ESCALATION_ALERT_TEMPLATE", "owner_escalation_alert")
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_escalation_alert("79991234567", "whatsapp")
    body = mock_post.call_args.kwargs["json"]
    assert body["type"] == "template"
    assert body["template"]["name"] == "owner_escalation_alert"
    params = [p["text"] for p in body["template"]["components"][0]["parameters"]]
    assert params == ["whatsapp", "79991234567"]


def test_send_owner_alert_falls_back_to_text_without_template():
    """No template env -> free-form text (the existing 24h-window behaviour)."""
    with patch("core.notify.requests.post") as mock_post:
        _mock_post_setup(mock_post)
        send_owner_alert("79991234567", "whatsapp", BOOKING)
    assert mock_post.call_args.kwargs["json"]["type"] == "text"
