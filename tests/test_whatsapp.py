import hashlib
import hmac as hmac_module
from unittest.mock import patch
from platforms.whatsapp import parse_inbound, verify_signature, send_reply

# --- parse_inbound ---

VALID_PAYLOAD = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": "79991234567",
                    "type": "text",
                    "text": {"body": "Здравствуйте"},
                }]
            }
        }]
    }]
}


def test_parse_inbound_returns_phone_and_text():
    phone, text = parse_inbound(VALID_PAYLOAD)
    assert phone == "79991234567"
    assert text == "Здравствуйте"


def test_parse_inbound_returns_none_for_non_text_message():
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{"from": "123", "type": "image"}]}}]}]
    }
    assert parse_inbound(payload) is None


def test_parse_inbound_returns_none_for_malformed_payload():
    assert parse_inbound({}) is None
    assert parse_inbound({"entry": []}) is None


# --- verify_signature ---

def _make_sig(secret: str, payload: bytes) -> str:
    h = hmac_module.new(secret.encode(), payload, hashlib.sha256)
    return f"sha256={h.hexdigest()}"


def test_verify_signature_accepts_valid_signature():
    payload = b'{"test": "data"}'
    sig = _make_sig("test-app-secret", payload)
    assert verify_signature(payload, sig, "test-app-secret") is True


def test_verify_signature_rejects_wrong_secret():
    payload = b'{"test": "data"}'
    sig = _make_sig("wrong-secret", payload)
    assert verify_signature(payload, sig, "test-app-secret") is False


def test_verify_signature_rejects_missing_prefix():
    assert verify_signature(b"data", "invalidsignature", "secret") is False


def test_verify_signature_rejects_tampered_payload():
    sig = _make_sig("test-app-secret", b"original")
    assert verify_signature(b"tampered", sig, "test-app-secret") is False


# --- send_reply ---

def test_send_reply_posts_to_correct_endpoint():
    with patch("platforms.whatsapp.requests.post") as mock_post:
        send_reply("79991234567", "Добрый день!")
    url = mock_post.call_args.args[0]
    assert "123456789" in url   # WHATSAPP_PHONE_NUMBER_ID from conftest


def test_send_reply_sends_correct_payload():
    with patch("platforms.whatsapp.requests.post") as mock_post:
        send_reply("79991234567", "Добрый день!")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["to"] == "79991234567"
    assert payload["text"]["body"] == "Добрый день!"
    assert payload["messaging_product"] == "whatsapp"


def test_verify_signature_rejects_empty_secret():
    payload = b"data"
    sig = _make_sig("", payload)   # valid sig for empty secret
    assert verify_signature(payload, sig, "") is False


def test_send_reply_uses_timeout():
    with patch("platforms.whatsapp.requests.post") as mock_post:
        send_reply("79991234567", "Добрый день!")
    _, kwargs = mock_post.call_args
    assert kwargs.get("timeout") == (3, 10)
