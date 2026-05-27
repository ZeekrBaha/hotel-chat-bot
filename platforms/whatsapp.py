import hashlib
import hmac
import os
import requests


def parse_inbound(payload: dict) -> tuple[str, str] | None:
    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        if message["type"] != "text":
            return None
        return message["from"], message["text"]["body"]
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


def send_reply(phone_number: str, text: str) -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    requests.post(
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
