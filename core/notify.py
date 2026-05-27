import logging
import os
import requests

_logger = logging.getLogger(__name__)


def send_owner_alert(sender_id: str, platform: str, booking: dict) -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    owner_number = os.environ["OWNER_PHONE_NUMBER"]

    body = (
        f"Новая заявка на бронирование\n"
        f"Гость: {booking['guest_name']}\n"
        f"Заезд: {booking['check_in']}\n"
        f"Выезд: {booking['check_out']}\n"
        f"Кол-во гостей: {booking['num_guests']}\n"
        f"Платформа: {platform}\n"
        f"Контакт: {sender_id}"
    )

    response = requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": owner_number,
            "type": "text",
            "text": {"body": body},
        },
        timeout=(3, 10),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Meta API error: {payload['error']}")


def send_escalation_alert(sender_id: str, platform: str) -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    owner_number = os.environ["OWNER_PHONE_NUMBER"]

    body = (
        f"Гость превысил дневной лимит сообщений.\n"
        f"Платформа: {platform}\n"
        f"Контакт: {sender_id}"
    )

    response = requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": owner_number,
            "type": "text",
            "text": {"body": body},
        },
        timeout=(3, 10),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Meta API error: {payload['error']}")
