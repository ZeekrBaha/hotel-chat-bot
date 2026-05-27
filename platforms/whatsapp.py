import hashlib
import hmac
import logging
import os
import requests


_logger = logging.getLogger(__name__)


def parse_inbound(payload: dict) -> tuple[str, str, str] | None:
    """Returns (phone, text, message_id) or None."""
    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        if message["type"] != "text":
            return None
        if "@g.us" in message.get("from", ""):
            return None
        return message["from"], message["text"]["body"], message["id"]
    except (KeyError, IndexError):
        return None


def verify_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    if not secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    received = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


def send_reply(phone_number: str, text: str) -> bool:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    resp = requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": text},
        },
        timeout=(3, 10),
    )
    if not resp.ok:
        _logger.error(
            "send_reply_failed status=%d body=%s",
            resp.status_code, resp.text[:200],
        )
        return False
    return True
