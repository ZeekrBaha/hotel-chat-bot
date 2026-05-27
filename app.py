import os
from flask import Flask, request
from core import bot, notify
from platforms import whatsapp

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

    if bot.is_booking_intent(text):
        notify.send_owner_alert(phone, "whatsapp", text, reply)

    whatsapp.send_reply(phone, reply)
    return "", 200
