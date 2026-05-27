import datetime
import functools
import json
import logging
import os
import time
from openai import OpenAI
from core import db

BOOKING_KEYWORDS = [
    "забронировать", "бронь", "свободен", "хочу номер",
    "book", "reserve", "бронирование",
]
CONTEXT_WINDOW = 10
_logger = logging.getLogger(__name__)
_openai_client: OpenAI | None = None

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "hotel_bot_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
                "is_booking_intent": {"type": "boolean"},
                "guest_name": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
            },
            "required": ["reply", "is_booking_intent", "guest_name"],
            "additionalProperties": False,
        },
    },
}


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            timeout=10.0,
            max_retries=2,
        )
    return _openai_client


@functools.lru_cache(maxsize=1)
def get_system_prompt() -> str:
    path = os.environ.get("SYSTEM_PROMPT_PATH", "system-prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _today() -> str:
    return datetime.date.today().strftime("%d.%m.%Y")


def is_booking_intent(message_text: str) -> bool:
    text_lower = message_text.lower()
    return any(kw in text_lower for kw in BOOKING_KEYWORDS)


def handle_message(platform: str, sender_id: str, message_text: str) -> dict:
    history = db.get_history(platform, sender_id)
    history.append({"role": "user", "content": message_text})

    client = _get_openai_client()
    t0 = time.monotonic()
    system_prompt = f"Сегодня: {_today()}\n\n{get_system_prompt()}"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=400,
        response_format=_RESPONSE_FORMAT,
        messages=[
            {"role": "system", "content": system_prompt},
            *history[-CONTEXT_WINDOW:],
        ],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    usage = response.usage
    _logger.info(
        "openai model=gpt-4o-mini tokens_in=%d tokens_out=%d latency_ms=%d",
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
        latency_ms,
    )

    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = {}

    reply = parsed.get("reply") or "Извините, не могу ответить на этот вопрос."
    booking_intent = parsed.get("is_booking_intent", False) or is_booking_intent(message_text)
    guest_name = parsed.get("guest_name")

    history.append({"role": "assistant", "content": reply})
    db.save_history(platform, sender_id, history)

    return {
        "reply": reply,
        "is_booking_intent": booking_intent,
        "guest_name": guest_name,
    }
