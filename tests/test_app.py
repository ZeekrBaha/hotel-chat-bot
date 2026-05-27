import hashlib
import hmac as hmac_module
import json
from unittest.mock import patch
import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _sign(payload: bytes, secret: str) -> str:
    h = hmac_module.new(secret.encode(), payload, hashlib.sha256)
    return f"sha256={h.hexdigest()}"


# --- GET /health ---

def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


# --- GET /whatsapp/webhook (Meta verification) ---

def test_whatsapp_verify_returns_challenge_on_valid_token(client):
    response = client.get(
        "/whatsapp/webhook",
        query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "hotel-bot-verify-2026",
            "hub.challenge": "abc123",
        },
    )
    assert response.status_code == 200
    assert response.data == b"abc123"


def test_whatsapp_verify_returns_403_on_wrong_token(client):
    response = client.get(
        "/whatsapp/webhook",
        query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "abc123",
        },
    )
    assert response.status_code == 403


# --- POST /whatsapp/webhook ---

def _inbound_payload(phone="79991234567", text="Здравствуйте"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": phone,
                        "type": "text",
                        "text": {"body": text},
                    }]
                }
            }]
        }]
    }


def test_whatsapp_inbound_rejects_bad_signature(client):
    payload = json.dumps(_inbound_payload()).encode()
    response = client.post(
        "/whatsapp/webhook",
        data=payload,
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=badsignature"},
    )
    assert response.status_code == 401


def test_whatsapp_inbound_returns_200_and_calls_bot(client):
    payload = json.dumps(_inbound_payload()).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message", return_value="Добрый день!") as mock_bot, \
         patch("app.whatsapp.send_reply") as mock_send, \
         patch("app.bot.is_booking_intent", return_value=False):

        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_bot.assert_called_once_with("whatsapp", "79991234567", "Здравствуйте")
    mock_send.assert_called_once_with("79991234567", "Добрый день!")


def test_whatsapp_inbound_sends_owner_alert_on_booking_intent(client):
    payload = json.dumps(_inbound_payload(text="Хочу забронировать")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message", return_value="Уточните даты."), \
         patch("app.whatsapp.send_reply"), \
         patch("app.bot.is_booking_intent", return_value=True), \
         patch("app.notify.send_owner_alert") as mock_notify:

        client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    mock_notify.assert_called_once_with(
        "79991234567", "whatsapp", "Хочу забронировать", "Уточните даты."
    )


def test_whatsapp_inbound_sends_reply_even_if_owner_alert_fails(client):
    payload = json.dumps(_inbound_payload(text="Хочу забронировать")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message", return_value="Уточните даты."), \
         patch("app.bot.is_booking_intent", return_value=True), \
         patch("app.notify.send_owner_alert", side_effect=Exception("network error")), \
         patch("app.whatsapp.send_reply") as mock_send:

        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_send.assert_called_once_with("79991234567", "Уточните даты.")


def test_whatsapp_inbound_returns_200_for_non_text_message(client):
    payload = json.dumps({
        "entry": [{"changes": [{"value": {"messages": [{"from": "123", "type": "image"}]}}]}]
    }).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message") as mock_bot:
        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_bot.assert_not_called()
