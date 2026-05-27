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


def _bot_result(
    reply: str,
    is_booking: bool = False,
    guest_name: str | None = None,
    check_in: str | None = None,
    check_out: str | None = None,
    num_guests: int | None = None,
    escalated: bool = False,
) -> dict:
    return {
        "reply": reply,
        "is_booking_intent": is_booking,
        "guest_name": guest_name,
        "check_in": check_in,
        "check_out": check_out,
        "num_guests": num_guests,
        "escalated": escalated,
    }


def _complete_booking_result(reply: str = "Бронь подтверждена.") -> dict:
    return _bot_result(
        reply=reply,
        is_booking=True,
        guest_name="Айгуль",
        check_in="2026-06-05",
        check_out="2026-06-07",
        num_guests=2,
    )


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

def _inbound_payload(phone="79991234567", text="Здравствуйте", msg_id="wamid.test"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "id": msg_id,
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


class _SyncThread:
    """Fake Thread that runs the target immediately on .start() (synchronous)."""
    def __init__(self, target, args, daemon=True):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def test_whatsapp_inbound_returns_200_and_calls_bot(client):
    payload = json.dumps(_inbound_payload(msg_id="wamid.bot1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_bot_result("Добрый день!")) as mock_bot, \
         patch("app.whatsapp.send_reply", return_value=True) as mock_send:

        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_bot.assert_called_once_with("whatsapp", "79991234567", "Здравствуйте")
    mock_send.assert_called_once_with("79991234567", "Добрый день!")


def test_whatsapp_inbound_sends_alert_when_all_booking_slots_filled(client):
    payload = json.dumps(_inbound_payload(text="Айгуль, 5-7 июня, 2 человека", msg_id="wamid.alert1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_complete_booking_result()), \
         patch("app.whatsapp.send_reply", return_value=True), \
         patch("app.db.check_and_set_booking_alert", return_value=True), \
         patch("app.notify.send_owner_alert") as mock_notify:

        client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    mock_notify.assert_called_once_with(
        "79991234567",
        "whatsapp",
        {
            "guest_name": "Айгуль",
            "check_in": "2026-06-05",
            "check_out": "2026-06-07",
            "num_guests": 2,
        },
    )


def test_whatsapp_inbound_no_alert_when_booking_slots_incomplete(client):
    payload = json.dumps(_inbound_payload(text="Хочу забронировать", msg_id="wamid.partial1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_bot_result("Уточните даты.", is_booking=True)), \
         patch("app.whatsapp.send_reply", return_value=True), \
         patch("app.notify.send_owner_alert") as mock_notify:

        client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    mock_notify.assert_not_called()


def test_whatsapp_inbound_no_alert_when_same_booking_already_alerted(client):
    payload = json.dumps(_inbound_payload(text="Спасибо!", msg_id="wamid.repeat1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_complete_booking_result()), \
         patch("app.whatsapp.send_reply", return_value=True), \
         patch("app.db.check_and_set_booking_alert", return_value=False), \
         patch("app.notify.send_owner_alert") as mock_notify:

        client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    mock_notify.assert_not_called()


def test_whatsapp_inbound_sends_reply_even_if_owner_alert_fails(client):
    payload = json.dumps(_inbound_payload(text="Айгуль, 5-7 июня, 2 человека", msg_id="wamid.fail1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_complete_booking_result("Ваша бронь подтверждена.")), \
         patch("app.db.check_and_set_booking_alert", return_value=True), \
         patch("app.notify.send_owner_alert", side_effect=Exception("network error")), \
         patch("app.whatsapp.send_reply", return_value=True) as mock_send:

        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_send.assert_called_once_with("79991234567", "Ваша бронь подтверждена.")


def test_whatsapp_inbound_sends_escalation_alert_when_limit_exceeded(client):
    payload = json.dumps(_inbound_payload(msg_id="wamid.esc1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_bot_result(
             "Передаю администратору.", escalated=True
         )), \
         patch("app.whatsapp.send_reply", return_value=True) as mock_send, \
         patch("app.notify.send_escalation_alert") as mock_escalate:

        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_send.assert_called_once_with("79991234567", "Передаю администратору.")
    mock_escalate.assert_called_once_with("79991234567", "whatsapp")


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


def test_whatsapp_inbound_caps_message_at_1000_chars(client):
    long_text = "а" * 1500
    payload = json.dumps(_inbound_payload(text=long_text, msg_id="wamid.cap1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", return_value=False), \
         patch("app.bot.handle_message", return_value=_bot_result("reply")) as mock_bot, \
         patch("app.whatsapp.send_reply", return_value=True):
        client.post("/whatsapp/webhook", data=payload,
                    content_type="application/json",
                    headers={"X-Hub-Signature-256": sig})

    actual_text = mock_bot.call_args.args[2]
    assert len(actual_text) == 1000
    assert actual_text == "а" * 1000


def test_health_deep_returns_200_when_all_healthy(client):
    with patch("app.db.get_client"), \
         patch("app.bot._get_openai_client"):
        response = client.get("/health/deep")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["checks"]["supabase"] == "ok"
    assert data["checks"]["openai"] == "ok"


def test_health_deep_returns_503_when_supabase_fails(client):
    with patch("app.db.get_client", side_effect=Exception("connection refused")), \
         patch("app.bot._get_openai_client"):
        response = client.get("/health/deep")
    assert response.status_code == 503
    data = response.get_json()
    assert data["status"] == "degraded"
    assert "connection refused" in data["checks"]["supabase"]


def test_whatsapp_inbound_deduplicates_retried_message(client):
    payload = json.dumps(_inbound_payload(msg_id="wamid.dup1")).encode()
    sig = _sign(payload, "test-app-secret")

    call_count = {"n": 0}
    def fake_is_duplicate(message_id):
        call_count["n"] += 1
        return call_count["n"] > 1  # False first call, True second call

    with patch("app.Thread", _SyncThread), \
         patch("app.db.is_duplicate_message", side_effect=fake_is_duplicate), \
         patch("app.bot.handle_message", return_value=_bot_result("reply")) as mock_bot, \
         patch("app.whatsapp.send_reply", return_value=True):
        client.post("/whatsapp/webhook", data=payload,
                    content_type="application/json",
                    headers={"X-Hub-Signature-256": sig})
        response = client.post("/whatsapp/webhook", data=payload,
                               content_type="application/json",
                               headers={"X-Hub-Signature-256": sig})

    assert response.status_code == 200
    assert mock_bot.call_count == 1
