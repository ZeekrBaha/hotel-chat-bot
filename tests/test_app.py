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


# --- POST /whatsapp/webhook (enqueue only; the worker does the real work) ---

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


def test_whatsapp_inbound_enqueues_message(client):
    payload = json.dumps(_inbound_payload(msg_id="wamid.enq1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.db.enqueue_message", return_value=True) as mock_enqueue:
        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_enqueue.assert_called_once_with("wamid.enq1", "whatsapp", "79991234567", "Здравствуйте")


def test_whatsapp_inbound_returns_200_for_duplicate(client):
    payload = json.dumps(_inbound_payload(msg_id="wamid.dup1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.db.enqueue_message", return_value=False) as mock_enqueue:
        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_enqueue.assert_called_once()


def test_whatsapp_inbound_returns_500_when_enqueue_fails(client):
    """A failed enqueue returns 500 so Meta retries the webhook — the message is
    not silently lost."""
    payload = json.dumps(_inbound_payload(msg_id="wamid.err1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.db.enqueue_message", side_effect=Exception("db down")):
        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 500


def test_whatsapp_inbound_returns_200_for_non_text_message(client):
    payload = json.dumps({
        "entry": [{"changes": [{"value": {"messages": [{"from": "123", "type": "image"}]}}]}]
    }).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.db.enqueue_message") as mock_enqueue:
        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_enqueue.assert_not_called()


def test_whatsapp_inbound_caps_message_at_1000_chars(client):
    long_text = "а" * 1500
    payload = json.dumps(_inbound_payload(text=long_text, msg_id="wamid.cap1")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.db.enqueue_message", return_value=True) as mock_enqueue:
        client.post("/whatsapp/webhook", data=payload,
                    content_type="application/json",
                    headers={"X-Hub-Signature-256": sig})

    enqueued_text = mock_enqueue.call_args.args[3]
    assert len(enqueued_text) == 1000
    assert enqueued_text == "а" * 1000


# --- GET /health/ready (Supabase only; no OpenAI) ---

def test_health_ready_returns_200_and_does_not_call_openai(client):
    with patch("app.db.check_health"), \
         patch("app.bot.check_openai_health") as mock_openai:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    mock_openai.assert_not_called()


def test_health_ready_returns_503_when_supabase_fails(client):
    with patch("app.db.check_health", side_effect=Exception("connection refused")):
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.get_json()["status"] == "not_ready"


# --- GET /health/deep (real reachability checks) ---

def test_health_deep_returns_200_when_all_healthy(client):
    with patch("app.db.check_health"), \
         patch("app.bot.check_openai_health"):
        response = client.get("/health/deep")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["checks"]["supabase"] == "ok"
    assert data["checks"]["openai"] == "ok"


def test_health_deep_returns_503_when_supabase_fails(client):
    with patch("app.db.check_health", side_effect=Exception("connection refused")), \
         patch("app.bot.check_openai_health"):
        response = client.get("/health/deep")
    assert response.status_code == 503
    data = response.get_json()
    assert data["status"] == "degraded"
    assert "connection refused" in data["checks"]["supabase"]


def test_health_deep_returns_503_when_openai_fails(client):
    with patch("app.db.check_health"), \
         patch("app.bot.check_openai_health", side_effect=Exception("invalid api key")):
        response = client.get("/health/deep")
    assert response.status_code == 503
    data = response.get_json()
    assert data["status"] == "degraded"
    assert "invalid api key" in data["checks"]["openai"]
