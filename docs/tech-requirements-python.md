# Hotel Chat Bot — Technical Requirements
## Python + VPS + Supabase Stack — WhatsApp (v1 POC) + Telegram (v2)

**Version:** 1.1  
**Date:** 2026-05-27  
**Author:** Baha  
**Status:** Draft — pending review

---

## 1. Overview

A Python-based AI assistant for a small hotel. Guests send messages via WhatsApp or Telegram; the bot answers FAQs automatically using Claude Haiku and notifies the owner when a booking request is detected. All conversation history is stored in Supabase so the bot remembers context across messages and the owner can review all chats.

**Platform rollout:**
- **v1 (POC):** WhatsApp only — Meta WhatsApp Business Cloud API
- **v2:** Add Telegram — Telegram Bot API

The core bot logic (Claude, Supabase, booking intent) is shared. Only the webhook handler and send functions differ per platform.

---

## 2. System Architecture

```
Guest (WhatsApp)                    Guest (Telegram)
      │                                   │
      ▼                                   ▼
Meta WhatsApp Business API      Telegram Bot API
      │  POST /whatsapp/webhook          │  POST /telegram/webhook
      └──────────────┬───────────────────┘
                     ▼
           Flask Application (VPS)
                     │
          ┌──────────┴──────────┐
          │                     │
  platforms/                 core/
  whatsapp.py               bot.py ──────► Anthropic API (Claude Haiku)
  telegram.py               db.py  ──────► Supabase (PostgreSQL)
                            notify.py       conversations table
```

**Request flow (both platforms):**
1. Inbound message arrives at platform-specific webhook route
2. Platform module extracts `(sender_id, message_text)` and normalises it
3. `bot.py` handles Claude call + Supabase history (platform-agnostic)
4. Platform module sends the reply back via the correct API
5. `notify.py` sends owner alert via WhatsApp when booking intent is detected

---

## 3. Infrastructure

### 3.1 VPS

| Parameter | Specification |
|---|---|
| Provider | Hetzner Cloud (recommended) or any VPS |
| Plan | CX22 — 2 vCPU, 4GB RAM, 40GB SSD |
| Cost | €4–6/month |
| OS | Ubuntu 22.04 LTS |
| Region | EU (Falkenstein or Helsinki) |
| Public IP | Required (static, for webhooks) |

**Required services on VPS:**
- Python 3.11+
- gunicorn (WSGI server)
- nginx (reverse proxy, SSL termination)
- systemd service (auto-restart on crash/reboot)
- Certbot (free Let's Encrypt SSL — required by Meta for webhooks)

### 3.2 Domain

A domain name is required for HTTPS (Meta rejects non-HTTPS webhook URLs; Telegram also requires HTTPS for webhooks).

Options:
- Buy a cheap domain (~$10/year) from Namecheap or Cloudflare
- Point an A record to the VPS IP
- Use Certbot to get a free SSL certificate

Alternative (no domain): use Cloudflare Tunnel (free) to expose the Flask app over HTTPS without a domain.

### 3.3 Supabase

| Parameter | Specification |
|---|---|
| Plan | Free tier (500MB DB, 2GB bandwidth) |
| Region | Europe West |
| Auth | Service Role key (server-side only, never exposed) |
| Interface | Supabase Dashboard (owner can view all chats) |

---

## 4. File Structure

```
hotel-chat-bot/
├── app.py                      ← Flask app: routes for both platform webhooks
├── core/
│   ├── bot.py                  ← Claude Haiku call, system prompt, reply logic (shared)
│   ├── db.py                   ← Supabase: read/write conversation history (shared)
│   └── notify.py               ← Send owner alert via WhatsApp (shared)
├── platforms/
│   ├── whatsapp.py             ← WhatsApp webhook handler + send function (v1)
│   └── telegram.py             ← Telegram webhook handler + send function (v2)
├── system-prompt.txt           ← Hotel prompt (owner edits this)
├── requirements.txt            ← Python dependencies
├── .env.example                ← Environment variable template (no secrets)
├── .env                        ← Actual secrets (never committed to git)
├── .gitignore                  ← Excludes .env, __pycache__, etc.
├── deploy/
│   ├── hotel-chat-bot.service  ← systemd unit file
│   ├── nginx.conf              ← nginx reverse proxy config
│   └── deploy.md               ← Step-by-step VPS setup guide
├── sql/
│   └── schema.sql              ← Supabase table creation SQL
├── test-messages.txt           ← Manual test script
└── docs/
    ├── meta-setup.md           ← Meta WhatsApp Business API setup guide
    └── tech-requirements-python.md  ← This document
```

---

## 5. Database Schema (Supabase)

### Table: `conversations`

```sql
CREATE TABLE conversations (
  id            BIGSERIAL PRIMARY KEY,
  platform      TEXT NOT NULL DEFAULT 'whatsapp',  -- 'whatsapp' | 'telegram'
  sender_id     TEXT NOT NULL,                     -- phone number or Telegram chat_id
  messages      JSONB NOT NULL DEFAULT '[]',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX conversations_platform_sender_idx
  ON conversations (platform, sender_id);
```

**`messages` JSONB structure** (array of message objects):

```json
[
  { "role": "user",      "content": "Здравствуйте, сколько стоит номер?" },
  { "role": "assistant", "content": "Добрый день! Стандартный номер — 2500 сом/ночь." },
  { "role": "user",      "content": "Хочу забронировать" }
]
```

**Retention policy:** Keep last 20 messages per guest. Older messages are trimmed on each write.

**Key change from v1:** The unique key is now `(platform, sender_id)` instead of just `phone_number`. This means the same guest can have separate conversation threads on WhatsApp and Telegram.

---

## 6. API Integrations

### 6.1 Meta WhatsApp Business Cloud API (v1 POC)

| Parameter | Detail |
|---|---|
| Endpoint | `https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages` |
| Auth | Bearer token (permanent system user token) |
| Webhook path | `POST /whatsapp/webhook` |
| Webhook verification | `GET /whatsapp/webhook` — verify token handshake |
| Inbound event | `messages` subscription |
| Message type handled | `text` only (v1 — no media) |
| Signature verification | `X-Hub-Signature-256` HMAC on every POST |

**Webhook payload (inbound message):**
```json
{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "XXXXXXXXXXX",
          "text": { "body": "Здравствуйте" },
          "type": "text"
        }]
      }
    }]
  }]
}
```

### 6.2 Telegram Bot API (v2)

| Parameter | Detail |
|---|---|
| Endpoint | `https://api.telegram.org/bot{TOKEN}/sendMessage` |
| Auth | Bot token in URL path (from BotFather) |
| Webhook path | `POST /telegram/webhook` |
| Webhook registration | One-time `setWebhook` call pointing to the VPS URL |
| Inbound event | `message.text` updates |
| Message type handled | `text` only (v1 — no media) |
| Signature verification | `X-Telegram-Bot-Api-Secret-Token` header on every POST |
| Setup effort | Simpler than WhatsApp — no business verification, no Meta approval |

**Webhook payload (inbound message):**
```json
{
  "message": {
    "chat": { "id": 123456789 },
    "text": "Здравствуйте",
    "from": { "first_name": "Айгуль" }
  }
}
```

**Outbound send (reply to guest):**
```json
{
  "chat_id": 123456789,
  "text": "Добрый день! Чем могу помочь?"
}
```

### 6.3 Anthropic Claude Haiku

| Parameter | Detail |
|---|---|
| Model | `claude-haiku-4-5-20251001` |
| Max tokens | 400 (sufficient for hotel FAQ replies) |
| Context window | Last 10 messages from Supabase + system prompt |
| System prompt | Contents of `system-prompt.txt` (loaded at startup) |
| Languages | Russian (primary), Kyrgyz, English |
| Cost | ~$0.80/1M input tokens, ~$4.00/1M output tokens |
| Est. monthly cost | ~$1–2/month at 1,000 messages |

### 6.4 Supabase Python Client

| Parameter | Detail |
|---|---|
| Library | `supabase` (official Python client) |
| Auth | `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` |
| Operations | `upsert` (write), `select` (read) on `conversations` table |
| Connection | HTTP (no persistent connection needed at this scale) |

---

## 7. Environment Variables

All secrets stored in `.env` on the VPS. Never committed to git.

```bash
# Meta WhatsApp (v1)
WHATSAPP_ACCESS_TOKEN=        # Permanent system user token
WHATSAPP_PHONE_NUMBER_ID=     # Phone Number ID from Meta Developer Console
WHATSAPP_VERIFY_TOKEN=hotel-bot-verify-2026
WHATSAPP_APP_SECRET=          # App secret for X-Hub-Signature-256 verification

# Owner notification
OWNER_PHONE_NUMBER=XXXXXXXXXXX   # No + sign, no spaces

# Telegram (v2)
TELEGRAM_BOT_TOKEN=           # From BotFather (@BotFather on Telegram)
TELEGRAM_WEBHOOK_SECRET=      # Random string — set when registering the webhook

# Anthropic
ANTHROPIC_API_KEY=            # From console.anthropic.com

# Supabase
SUPABASE_URL=                 # https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=         # Service role key (not anon key)

# App
FLASK_ENV=production
PORT=8000
```

---

## 8. Application Logic

### 8.1 Flask Routes (`app.py`)

```
GET  /whatsapp/webhook    ← Meta webhook verification handshake
POST /whatsapp/webhook    ← Inbound WhatsApp messages (v1)

POST /telegram/webhook    ← Inbound Telegram messages (v2)

GET  /health              ← Health check (returns 200 OK)
```

### 8.2 WhatsApp Webhook Handler (`platforms/whatsapp.py`)

```
POST /whatsapp/webhook:
  1. Verify X-Hub-Signature-256 HMAC — reject if invalid
  2. Extract: sender phone number, message text, message type
  3. Ignore non-text messages — return 200 OK
  4. Ignore echo messages (from bot's own number)
  5. Call core/bot.py handle_message(platform="whatsapp", sender_id, text)
  6. Send reply via Meta Graph API
  7. Return 200 OK (must respond within 5 seconds — Meta retries otherwise)
```

### 8.3 Telegram Webhook Handler (`platforms/telegram.py`)

```
POST /telegram/webhook:
  1. Verify X-Telegram-Bot-Api-Secret-Token header — reject if invalid
  2. Extract: chat_id, message text, message type
  3. Ignore non-text updates (photos, stickers, etc.) — return 200 OK
  4. Call core/bot.py handle_message(platform="telegram", sender_id=chat_id, text)
  5. Send reply via Telegram sendMessage API
  6. Return 200 OK
```

### 8.4 Core Bot Logic (`core/bot.py`)

```
handle_message(platform, sender_id, message_text) → reply_text:
  1. Load conversation history from Supabase (platform + sender_id, last 10 messages)
  2. Append new user message to history
  3. Call Claude Haiku with system_prompt + history
  4. Append assistant reply to history
  5. Trim history to last 20 messages
  6. Save updated history to Supabase (upsert on platform + sender_id)
  7. Check for booking intent keywords in message_text:
       ["забронировать", "бронь", "свободен", "book", "reserve", "хочу номер"]
  8. If booking intent → call notify.py with sender_id + platform + message_text + reply
  9. Return reply text
```

### 8.5 Booking Intent & Owner Notification (`core/notify.py`)

Simple keyword match on the incoming message (case-insensitive). When triggered, send a WhatsApp message to the owner's number with:
- Guest identifier (phone number or Telegram name/chat_id)
- Source platform (WhatsApp / Telegram)
- Guest's message
- Bot's reply

### 8.6 Session Memory (`core/db.py`)

```
get_history(platform, sender_id) → list[dict]
  SELECT messages FROM conversations
  WHERE platform = $1 AND sender_id = $2
  Returns [] if no row exists

save_history(platform, sender_id, messages: list[dict])
  UPSERT into conversations (platform, sender_id, messages, updated_at)
  Trim messages to last 20 before saving
```

---

## 9. Security Requirements

| Requirement | Implementation |
|---|---|
| WhatsApp webhook authenticity | Verify `X-Hub-Signature-256` HMAC on every POST |
| Telegram webhook authenticity | Verify `X-Telegram-Bot-Api-Secret-Token` header on every POST |
| Secrets management | `.env` file on VPS, never in git |
| HTTPS only | nginx with Let's Encrypt certificate |
| Supabase key | Service role key used server-side only |
| No payment data | System prompt explicitly prohibits bot from sharing payment details |
| Input sanitization | Strip and truncate incoming message text (max 4,000 chars) |

---

## 10. Deployment

### 10.1 VPS Setup (one-time)

```bash
# 1. Update system
apt update && apt upgrade -y

# 2. Install Python and tools
apt install python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx -y

# 3. Create app user
useradd -m -s /bin/bash hotelbot

# 4. Clone repo and install deps
su - hotelbot
git clone https://github.com/ZeekrBaha/hotel-chat-bot.git
cd hotel-chat-bot
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Create .env with real values
cp .env.example .env
nano .env

# 6. Install systemd service
cp deploy/hotel-chat-bot.service /etc/systemd/system/
systemctl enable hotel-chat-bot
systemctl start hotel-chat-bot

# 7. Configure nginx
cp deploy/nginx.conf /etc/nginx/sites-available/hotel-chat-bot
ln -s /etc/nginx/sites-available/hotel-chat-bot /etc/nginx/sites-enabled/
certbot --nginx -d yourdomain.com
systemctl reload nginx
```

### 10.2 Register Telegram Webhook (one-time, after VPS is live)

```bash
curl "https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://yourdomain.com/telegram/webhook" \
  -d "secret_token={TELEGRAM_WEBHOOK_SECRET}"
```

### 10.3 Updating the Bot

```bash
# On VPS
su - hotelbot
cd hotel-chat-bot
git pull
source venv/bin/activate
pip install -r requirements.txt  # if deps changed
systemctl restart hotel-chat-bot
```

### 10.4 Updating the System Prompt (No Redeploy Needed)

Edit `system-prompt.txt` on the VPS and restart the service:
```bash
nano system-prompt.txt
systemctl restart hotel-chat-bot
```

---

## 11. Python Dependencies (`requirements.txt`)

```
flask==3.0.3
gunicorn==21.2.0
anthropic==0.25.0
supabase==2.4.2
python-dotenv==1.0.1
requests==2.31.0
```

No extra library needed for Telegram — the Bot API is plain HTTP (handled by `requests`).

---

## 12. Cost Summary

| Item | Monthly Cost |
|---|---|
| Hetzner VPS CX22 | ~€4 (~$4.50) |
| Domain (optional) | ~$1 (amortized) |
| Supabase | $0 (free tier) |
| Meta WhatsApp service conversations | $0 (free for inbound) |
| Telegram | $0 (Bot API is free) |
| Anthropic Claude Haiku (1,000 msg) | ~$2 |
| **Total** | **~$7–8/month** |

At 5,000 messages/month across both platforms: ~$15–20/month.

---

## 13. Functional Requirements

### v1 — WhatsApp POC

| ID | Requirement |
|---|---|
| FR-1 | Bot replies to WhatsApp messages in Russian within 5 seconds |
| FR-2 | Bot answers FAQs: prices, room types, check-in/out, amenities, directions |
| FR-3 | Bot detects booking intent via keyword matching |
| FR-4 | Bot collects: guest name, check-in date, check-out date, number of guests |
| FR-5 | Bot notifies owner via WhatsApp when booking intent is detected |
| FR-6 | Bot responds in Kyrgyz if guest writes in Kyrgyz |
| FR-7 | Bot responds in English if guest writes in English |
| FR-8 | Bot never shares payment details |
| FR-9 | Conversation history persists across messages (Supabase) |
| FR-10 | System prompt is editable without redeploying code |

### v2 — Telegram Addition

| ID | Requirement |
|---|---|
| FR-11 | Same FAQ + booking flow available on Telegram |
| FR-12 | Conversation history is per-platform (WhatsApp and Telegram threads are independent) |
| FR-13 | Owner notification includes source platform (WhatsApp vs Telegram) |

---

## 14. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Uptime: 99%+ (systemd auto-restart) |
| NFR-2 | Response time: under 5 seconds end-to-end on both platforms |
| NFR-3 | Cost: under $20/month at peak volume across both platforms |
| NFR-4 | All secrets in environment variables, never in code |
| NFR-5 | HTTPS required (Meta and Telegram webhook requirement) |
| NFR-6 | Webhook signature verification on every inbound request |

---

## 15. Out of Scope

- Voice/phone call handling (Phase 3 — VAPI + Zadarma)
- Media messages (images, voice notes, documents)
- Booking calendar / availability database
- Payment processing
- Admin dashboard beyond Supabase table viewer
- Multi-hotel support
