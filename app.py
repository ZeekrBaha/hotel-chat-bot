import logging
import os
from flask import Flask, request
from core import bot, db
from platforms import whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
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

MAX_MESSAGE_LEN = 1000

app = Flask(__name__)
_logger = logging.getLogger(__name__)


@app.get("/health")
def health():
    """Liveness: the process is up. Cheap — safe for frequent probes."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    """Readiness: can we serve traffic? Checks Supabase (a single indexed query)
    but NOT OpenAI, so load balancers can probe this often without hitting the
    OpenAI API. Use /health/deep for full diagnostics."""
    try:
        db.check_health()
    except Exception as e:
        return {"status": "not_ready", "checks": {"supabase": f"error: {e}"}}, 503
    return {"status": "ready", "checks": {"supabase": "ok"}}


@app.get("/health/deep")
def health_deep():
    """Full diagnostics with REAL reachability checks, including a live OpenAI
    call. Heavier — for humans / monitoring, not for per-request LB probes."""
    checks = {}
    try:
        db.check_health()
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {e}"
    try:
        bot.check_openai_health()
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


@app.post("/whatsapp/webhook")
def whatsapp_inbound():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not whatsapp.verify_signature(request.data, sig, os.environ["WHATSAPP_APP_SECRET"]):
        return "Unauthorized", 401

    parsed = whatsapp.parse_inbound(request.json)
    if parsed is None:
        return "", 200

    phone, text, message_id = parsed
    text = text[:MAX_MESSAGE_LEN]

    # Only enqueue. A separate worker (python -m core.worker) does the real work,
    # so a failed OpenAI/WhatsApp/Supabase call retries instead of dropping the
    # message. enqueue_message dedups Meta retries atomically (returns False).
    try:
        enqueued = db.enqueue_message(message_id, "whatsapp", phone, text)
    except Exception:
        _logger.exception("enqueue_failed message_id=%s", message_id)
        # 500 -> Meta retries the webhook, so the message is not lost.
        return "", 500

    if not enqueued:
        _logger.info("duplicate_message_ignored message_id=%s", message_id)
    return "", 200
