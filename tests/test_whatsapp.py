import hashlib
import hmac as hmac_module
from unittest.mock import patch
from platforms.whatsapp import parse_inbound, verify_signature, send_reply, is_duplicate
import platforms.whatsapp as wa_module

# --- parse_inbound ---

VALID_PAYLOAD = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "id": "wamid.test123",
                    "from": "79991234567",
                    "type": "text",
                    "text": {"body": "Здравствуйте"},
                }]
            }
        }]
    }]
}


def test_parse_inbound_returns_phone_text_and_message_id():
    phone, text, message_id = parse_inbound(VALID_PAYLOAD)
    assert phone == "79991234567"
    assert text == "Здравствуйте"
    assert message_id == "wamid.test123"


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


# --- is_duplicate ---

def test_is_duplicate_returns_false_first_time():
    wa_module._seen_message_ids.clear()
    assert is_duplicate("wamid.unique1") is False


def test_is_duplicate_returns_true_second_time():
    wa_module._seen_message_ids.clear()
    is_duplicate("wamid.unique2")
    assert is_duplicate("wamid.unique2") is True


def test_is_duplicate_different_ids_not_duplicate():
    wa_module._seen_message_ids.clear()
    is_duplicate("wamid.a")
    assert is_duplicate("wamid.b") is False


def test_parse_inbound_skips_group_message():
    group_payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "id": "wamid.group1",
                        "from": "120363123456789@g.us",
                        "type": "text",
                        "text": {"body": "Hello group"},
                    }]
                }
            }]
        }]
    }
    assert parse_inbound(group_payload) is None
