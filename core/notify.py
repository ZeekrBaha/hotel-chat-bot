import os
import requests


def send_owner_alert(
    sender_id: str, platform: str, message_text: str, bot_reply: str
) -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    owner_number = os.environ["OWNER_PHONE_NUMBER"]

    body = (
        f"Новая заявка на бронирование\n"
        f"Платформа: {platform}\n"
        f"Гость: {sender_id}\n"
        f"Сообщение: {message_text}\n"
        f"Ответ бота: {bot_reply}"
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
    )
    response.raise_for_status()
