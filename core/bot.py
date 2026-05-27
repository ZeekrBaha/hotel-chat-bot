import os
from openai import OpenAI
from core import db

BOOKING_KEYWORDS = [
    "забронировать", "бронь", "свободен", "хочу номер",
    "book", "reserve", "бронирование",
]
CONTEXT_WINDOW = 10


def get_system_prompt() -> str:
    path = os.environ.get("SYSTEM_PROMPT_PATH", "system-prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def is_booking_intent(message_text: str) -> bool:
    text_lower = message_text.lower()
    return any(kw in text_lower for kw in BOOKING_KEYWORDS)


def handle_message(platform: str, sender_id: str, message_text: str) -> str:
    history = db.get_history(platform, sender_id)
    history.append({"role": "user", "content": message_text})

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=400,
        messages=[
            {"role": "system", "content": get_system_prompt()},
            *history[-CONTEXT_WINDOW:],
        ],
    )
    reply = response.choices[0].message.content

    history.append({"role": "assistant", "content": reply})
    db.save_history(platform, sender_id, history)

    return reply
