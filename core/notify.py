import logging
import os
import requests

_logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v19.0"


def _post(payload: dict) -> None:
    """Send one message via the WhatsApp Cloud API. Raises on transport or Meta
    API errors so the caller can decide whether the failure is fatal."""
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    response = requests.post(
        f"{GRAPH_URL}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=(3, 10),
    )
    response.raise_for_status()
    try:
        body = response.json()
    except Exception:
        body = {}
    if body.get("error"):
        raise RuntimeError(f"Meta API error: {body['error']}")


def _text_payload(to: str, body: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }


def _template_payload(to: str, name: str, lang: str, params: list) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": lang},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            }],
        },
    }


def _template_lang() -> str:
    return os.environ.get("WHATSAPP_TEMPLATE_LANG", "ru")


def send_owner_alert(sender_id: str, platform: str, booking: dict) -> None:
    """Notify the owner of a completed booking request.

    Uses an approved WhatsApp template when OWNER_ALERT_TEMPLATE is set (works
    outside Meta's 24h customer-service window). Falls back to a free-form text
    message otherwise (only deliverable inside the 24h window).
    """
    owner_number = os.environ["OWNER_PHONE_NUMBER"]
    template = os.environ.get("OWNER_ALERT_TEMPLATE")

    if template:
        params = [
            booking["guest_name"],
            booking["check_in"],
            booking["check_out"],
            booking["num_guests"],
            platform,
            sender_id,
        ]
        _post(_template_payload(owner_number, template, _template_lang(), params))
        return

    body = (
        f"Новая заявка на бронирование\n"
        f"Гость: {booking['guest_name']}\n"
        f"Заезд: {booking['check_in']}\n"
        f"Выезд: {booking['check_out']}\n"
        f"Кол-во гостей: {booking['num_guests']}\n"
        f"Платформа: {platform}\n"
        f"Контакт: {sender_id}"
    )
    _post(_text_payload(owner_number, body))


def send_escalation_alert(sender_id: str, platform: str) -> None:
    """Notify the owner that a guest exceeded the daily message limit.

    Uses an approved WhatsApp template when ESCALATION_ALERT_TEMPLATE is set,
    otherwise falls back to free-form text (24h-window only).
    """
    owner_number = os.environ["OWNER_PHONE_NUMBER"]
    template = os.environ.get("ESCALATION_ALERT_TEMPLATE")

    if template:
        _post(_template_payload(owner_number, template, _template_lang(), [platform, sender_id]))
        return

    body = (
        f"Гость превысил дневной лимит сообщений.\n"
        f"Платформа: {platform}\n"
        f"Контакт: {sender_id}"
    )
    _post(_text_payload(owner_number, body))
