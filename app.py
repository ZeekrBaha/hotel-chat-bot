import datetime
import hashlib
import logging
import os
import signal
import time
import threading
from threading import Thread
from flask import Flask, request
from core import bot, db, notify
from platforms import whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

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
    if not os.environ.get(_key):
        raise RuntimeError(f"Missing required environment variable: {_key}")

app = Flask(__name__)
_logger = logging.getLogger(__name__)

_inflight: set[threading.Thread] = set()
_inflight_lock = threading.Lock()


def _graceful_exit(signum, frame):
    _logger.info("SIGTERM received, waiting for %d in-flight requests...", len(_inflight))
    with _inflight_lock:
        inflight_copy = list(_inflight)
    for t in inflight_copy:
        t.join(timeout=15)
    _logger.info("graceful_exit complete")


signal.signal(signal.SIGTERM, _graceful_exit)


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:8]


def _booking_complete(result: dict) -> bool:
    try:
        datetime.date.fromisoformat(result["check_in"])
        datetime.date.fromisoformat(result["check_out"])
    except (TypeError, ValueError, KeyError):
        return False
    return (
        bool(result.get("guest_name"))
        and isinstance(result.get("num_guests"), int)
        and result["num_guests"] > 0
    )


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


def _process_whatsapp(phone: str, text: str, message_id: str) -> None:
    text = text[:1000]
    t0 = time.monotonic()
    phone_hash = _hash_phone(phone)
    current_thread = threading.current_thread()
    with _inflight_lock:
        _inflight.add(current_thread)
    try:
        result = bot.handle_message("whatsapp", phone, text)
        send_ok = whatsapp.send_reply(phone, result["reply"])
        if not send_ok:
            _logger.error("reply_undelivered phone=%s message_id=%s", phone_hash, message_id)
            return
        if result.get("escalated"):
            try:
                notify.send_escalation_alert(phone, "whatsapp")
            except Exception:
                _logger.exception("escalation_alert_failed phone=%s", phone_hash)
        elif _booking_complete(result):
            booking = {
                "guest_name": result["guest_name"],
                "check_in": result["check_in"],
                "check_out": result["check_out"],
                "num_guests": result["num_guests"],
            }
            if db.check_and_set_booking_alert("whatsapp", phone, booking):
                try:
                    notify.send_owner_alert(phone, "whatsapp", booking)
                except Exception:
                    _logger.exception("owner_alert_failed phone=%s", phone_hash)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _logger.info(
            "processed message_id=%s phone=%s booking=%s latency_ms=%d",
            message_id, phone_hash, result["is_booking_intent"], latency_ms,
        )
    except Exception:
        _logger.exception("process_error phone=%s message_id=%s", phone_hash, message_id)
    finally:
        with _inflight_lock:
            _inflight.discard(current_thread)


@app.post("/whatsapp/webhook")
def whatsapp_inbound():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not whatsapp.verify_signature(request.data, sig, os.environ["WHATSAPP_APP_SECRET"]):
        return "Unauthorized", 401

    parsed = whatsapp.parse_inbound(request.json)
    if parsed is None:
        return "", 200

    phone, text, message_id = parsed

    if db.is_duplicate_message(message_id):
        return "", 200

    t = Thread(target=_process_whatsapp, args=(phone, text, message_id), daemon=False)
    t.start()
    return "", 200
