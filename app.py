import os
from flask import Flask, request
from core import bot, notify
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


@app.get("/health")
def health():
    return {"status": "ok"}


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

    phone, text = parsed
    reply = bot.handle_message("whatsapp", phone, text)

    whatsapp.send_reply(phone, reply)

    if bot.is_booking_intent(text):
        try:
            notify.send_owner_alert(phone, "whatsapp", text, reply)
        except Exception:
            app.logger.exception("owner alert failed for %s", phone[:4] + "****")

    return "", 200
