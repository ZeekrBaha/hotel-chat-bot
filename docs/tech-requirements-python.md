# Hotel WhatsApp Bot — Technical Requirements
## Python + VPS + Supabase Stack

**Version:** 1.0  
**Date:** 2026-05-11  
**Author:** Baha  
**Status:** Draft — pending review

---

## 1. Overview

A Python-based WhatsApp AI assistant for a small hotel. Guests send WhatsApp messages in Russian or Kyrgyz; the bot answers FAQs automatically using Claude Haiku and notifies the owner when a booking request is detected. All conversation history is stored in Supabase so the bot remembers context across messages and the owner can review all chats.

---

## 2. System Architecture

```
Guest (WhatsApp)
      │
      ▼
Meta WhatsApp Business Cloud API
      │  POST /webhook
      ▼
Flask Application (VPS)
      │
      ├─── db.py ──────► Supabase (PostgreSQL)
      │                   conversations table
      │                   (per-guest message history)
      │
      ├─── bot.py ─────► Anthropic API (Claude Haiku 3.5)
      │                   Russian/Kyrgyz system prompt
      │                   last 10 messages as context
      │
      └─── notify ─────► Meta WhatsApp API
                          → sister's number (booking alerts)
                          → guest (reply)
```

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
| Public IP | Required (static, for webhook) |

**Required services on VPS:**
- Python 3.11+
- gunicorn (WSGI server)
- nginx (reverse proxy, SSL termination)
- systemd service (auto-restart on crash/reboot)
- Certbot (free Let's Encrypt SSL — required by Meta for webhooks)

### 3.2 Domain

A domain name is required for HTTPS (Meta rejects non-HTTPS webhook URLs).

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
hotel-bot/
├── app.py                  ← Flask app: webhook handler, routing
├── bot.py                  ← Claude Haiku call, system prompt, reply logic
├── db.py                   ← Supabase: read/write conversation history
├── notify.py               ← Send WhatsApp notification to sister
├── system-prompt.txt       ← Russian/Kyrgyz hotel prompt (owner edits this)
├── requirements.txt        ← Python dependencies
├── .env.example            ← Environment variable template (no secrets)
├── .env                    ← Actual secrets (never committed to git)
├── .gitignore              ← Excludes .env, __pycache__, etc.
├── deploy/
│   ├── hotel-bot.service   ← systemd unit file
│   ├── nginx.conf          ← nginx reverse proxy config
│   └── deploy.md           ← Step-by-step VPS setup guide
├── sql/
│   └── schema.sql          ← Supabase table creation SQL
├── test-messages.txt       ← Manual test script
└── docs/
    ├── meta-setup.md       ← Meta WhatsApp Business API setup guide
    └── tech-requirements-python.md  ← This document
```

---

## 5. Database Schema (Supabase)

### Table: `conversations`

```sql
CREATE TABLE conversations (
  id            BIGSERIAL PRIMARY KEY,
  phone_number  TEXT NOT NULL,
  messages      JSONB NOT NULL DEFAULT '[]',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX conversations_phone_number_idx
  ON conversations (phone_number);
```

**`messages` JSONB structure** (array of message objects):

```json
[
  { "role": "user",      "content": "Здравствуйте, сколько стоит номер?" },
  { "role": "assistant", "content": "Добрый день! Стандартный номер — 2500 сом/ночь." },
  { "role": "user",      "content": "Хочу забронировать" }
]
```

**Retention policy:** Keep last 20 messages per guest. Older messages are trimmed on each write to keep the array bounded.

---

## 6. API Integrations

### 6.1 Meta WhatsApp Business Cloud API

| Parameter | Detail |
|---|---|
| Endpoint | `https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages` |
| Auth | Bearer token (permanent system user token) |
| Webhook path | `POST /webhook` |
| Webhook verification | `GET /webhook` — verify token handshake |
| Inbound event | `messages` subscription |
| Message type handled | `text` only (v1 — no media) |
| Rate limit | 1,000 messages/second (not a concern at hotel scale) |

**Webhook payload (inbound message):**
```json
{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "996XXXXXXXXX",
          "text": { "body": "Здравствуйте" },
          "type": "text"
        }]
      }
    }]
  }]
}
```

**Outbound send (reply to guest):**
```json
{
  "messaging_product": "whatsapp",
  "to": "996XXXXXXXXX",
  "type": "text",
  "text": { "body": "Добрый день! Чем могу помочь?" }
}
```

### 6.2 Anthropic Claude Haiku 3.5

| Parameter | Detail |
|---|---|
| Model | `claude-haiku-4-5-20251001` |
| Max tokens | 400 (sufficient for hotel FAQ replies) |
| Context window | Last 10 messages from Supabase + system prompt |
| System prompt | Contents of `system-prompt.txt` (loaded at startup) |
| Languages | Russian (primary), Kyrgyz, English |
| Cost | ~$0.80/1M input tokens, ~$4.00/1M output tokens |
| Est. monthly cost | ~$1–2/month at 1,000 messages |

### 6.3 Supabase Python Client

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
# Meta WhatsApp
WHATSAPP_ACCESS_TOKEN=       # Permanent system user token
WHATSAPP_PHONE_NUMBER_ID=    # Phone Number ID from Meta Developer Console
WHATSAPP_VERIFY_TOKEN=hotel-bot-verify-2026

# Sister notification
SISTER_PHONE_NUMBER=996XXXXXXXXX  # No + sign, no spaces

# Anthropic
ANTHROPIC_API_KEY=           # From console.anthropic.com

# Supabase
SUPABASE_URL=                # https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=        # Service role key (not anon key)

# App
FLASK_ENV=production
PORT=8000
```

---

## 8. Application Logic

### 8.1 Webhook Handler (`app.py`)

```
POST /webhook
  1. Verify request is from Meta (check X-Hub-Signature-256 header)
  2. Extract: sender phone number, message text, message type
  3. Ignore non-text messages (images, voice notes, etc.) — return 200 OK
  4. Ignore messages from the bot's own number (echo prevention)
  5. Pass to bot.py → get reply text
  6. Send reply to guest via Meta API
  7. Return 200 OK to Meta (within 5 seconds — Meta retries if no 200)

GET /webhook
  1. Verify hub.verify_token matches WHATSAPP_VERIFY_TOKEN
  2. Return hub.challenge if valid
```

### 8.2 Bot Logic (`bot.py`)

```
handle_message(phone_number, message_text):
  1. Load conversation history from Supabase (last 10 messages)
  2. Append new user message to history
  3. Call Claude Haiku with system_prompt + history
  4. Append assistant reply to history
  5. Trim history to last 20 messages
  6. Save updated history to Supabase (upsert)
  7. Check for booking intent keywords in message_text:
       ["забронировать", "бронь", "свободен", "book", "reserve", "хочу номер"]
  8. If booking intent → call notify.py with phone_number + message_text + reply
  9. Return reply text
```

### 8.3 Booking Intent Detection

Simple keyword match on the incoming message (case-insensitive). When triggered:
- Send a WhatsApp message to the sister's number with:
  - Guest phone number
  - Guest's message
  - Bot's reply

This is a v1 implementation. A future version can use Claude to return structured JSON with `intent: booking` for more accurate detection.

### 8.4 Session Memory (`db.py`)

```
get_history(phone_number) → list[dict]
  SELECT messages FROM conversations WHERE phone_number = $1
  Returns [] if no row exists

save_history(phone_number, messages: list[dict])
  UPSERT into conversations (phone_number, messages, updated_at)
  Trim messages to last 20 before saving
```

---

## 9. Security Requirements

| Requirement | Implementation |
|---|---|
| Webhook authenticity | Verify `X-Hub-Signature-256` HMAC on every POST |
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
cp deploy/hotel-bot.service /etc/systemd/system/
systemctl enable hotel-bot
systemctl start hotel-bot

# 7. Configure nginx
cp deploy/nginx.conf /etc/nginx/sites-available/hotel-bot
ln -s /etc/nginx/sites-available/hotel-bot /etc/nginx/sites-enabled/
certbot --nginx -d yourdomain.com
systemctl reload nginx
```

### 10.2 Updating the Bot

```bash
# On VPS
su - hotelbot
cd hotel-chat-bot
git pull
source venv/bin/activate
pip install -r requirements.txt  # if deps changed
systemctl restart hotel-bot
```

### 10.3 Updating the System Prompt (No Redeploy Needed)

Edit `system-prompt.txt` on the VPS and restart the service:
```bash
nano system-prompt.txt
systemctl restart hotel-bot
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

---

## 12. Cost Summary

| Item | Monthly Cost |
|---|---|
| Hetzner VPS CX22 | ~€4 (~$4.50) |
| Domain (optional) | ~$1 (amortized) |
| Supabase | $0 (free tier) |
| Meta WhatsApp service conversations | $0 (free for inbound) |
| Anthropic Claude Haiku (1,000 msg) | ~$2 |
| **Total** | **~$7–8/month** |

At 5,000 messages/month (busier season): ~$15–20/month.

---

## 13. Functional Requirements

| ID | Requirement |
|---|---|
| FR-1 | Bot replies to WhatsApp messages in Russian within 5 seconds |
| FR-2 | Bot answers FAQs: prices, room types, check-in/out, amenities, directions |
| FR-3 | Bot detects booking intent via keyword matching |
| FR-4 | Bot collects: guest name, check-in date, check-out date, number of guests |
| FR-5 | Bot notifies sister via WhatsApp when booking intent is detected |
| FR-6 | Bot responds in Kyrgyz if guest writes in Kyrgyz |
| FR-7 | Bot responds in English if guest writes in English |
| FR-8 | Bot never shares payment details |
| FR-9 | Conversation history persists across messages (Supabase) |
| FR-10 | System prompt is editable without redeploying code |

---

## 14. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Uptime: 99%+ (systemd auto-restart) |
| NFR-2 | Response time: under 5 seconds end-to-end |
| NFR-3 | Cost: under $20/month at peak season volume |
| NFR-4 | All secrets in environment variables, never in code |
| NFR-5 | HTTPS required (Meta webhook requirement) |
| NFR-6 | Webhook signature verification on every request |

---

## 15. Out of Scope (v1)

- Voice/phone call handling (Phase 2 — VAPI + Zadarma)
- Media messages (images, voice notes, documents)
- Booking calendar / availability database
- Payment processing
- Admin dashboard beyond Supabase table viewer
- Multi-hotel support
