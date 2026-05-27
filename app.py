import hashlib
import logging
import os
import time
from threading import Thread
from flask import Flask, request
from core import bot, db, notify
from platforms import whatsapp

REQUIRED_ENV = [
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET",
    "OWNER_PHONE_NUMBER",
    "OPENAI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
]

for _key in REQUIRED_ENV:
    if _key not in os.environ:
        raise RuntimeError(f"Missing required environment variable: {_key}")

app = Flask(__name__)
_logger = logging.getLogger(__name__)


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:8]


def _booking_complete(result: dict) -> bool:
    return all([
        result.get("guest_name"),
        result.get("check_in"),
        result.get("check_out"),
        result.get("num_guests"),
    ])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/deep")
def health_deep():
    checks = {}
    try:
        db.get_client()
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {e}"
    try:
        bot._get_openai_client()
        checks["openai"] = "ok"
    except Exception as e:
        checks["openai"] = f"error: {e}"
    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}, (200 if all_ok else 503)


@app.get("/whatsapp/webhook")
def whatsapp_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.environ["WHATSAPP_VERIFY_TOKEN"]:
        return challenge, 200
    return "Forbidden", 403


def _process_whatsapp(payload: dict, phone: str, text: str, message_id: str) -> None:
    text = text[:1000]
    t0 = time.monotonic()
    phone_hash = _hash_phone(phone)
    try:
        result = bot.handle_message("whatsapp", phone, text)
        whatsapp.send_reply(phone, result["reply"])
        if result.get("escalated"):
            try:
                notify.send_escalation_alert(phone, "whatsapp")
            except Exception:
                _logger.exception("escalation_alert_failed phone=%s", phone_hash)
        elif _booking_complete(result):
            try:
                notify.send_owner_alert(phone, "whatsapp", {
                    "guest_name": result["guest_name"],
                    "check_in": result["check_in"],
                    "check_out": result["check_out"],
                    "num_guests": result["num_guests"],
                })
            except Exception:
                _logger.exception("owner_alert_failed phone=%s", phone_hash)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _logger.info(
            "processed message_id=%s phone=%s booking=%s latency_ms=%d",
            message_id, phone_hash, result["is_booking_intent"], latency_ms,
        )
    except Exception:
        _logger.exception("process_error phone=%s message_id=%s", phone_hash, message_id)


@app.post("/whatsapp/webhook")
def whatsapp_inbound():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not whatsapp.verify_signature(request.data, sig, os.environ["WHATSAPP_APP_SECRET"]):
        return "Unauthorized", 401

    parsed = whatsapp.parse_inbound(request.json)
    if parsed is None:
        return "", 200

    phone, text, message_id = parsed

    if whatsapp.is_duplicate(message_id):
        return "", 200

    Thread(target=_process_whatsapp, args=(request.json, phone, text, message_id), daemon=True).start()
    return "", 200
