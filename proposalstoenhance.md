# Proposals to Enhance — borrowed from `wassengerhq/whatsapp-chatgpt-bot`

Comparison of the wassenger reference bot (Node.js + Wassenger BSP, ~230KB) against your hotel-chat-bot (Python + Meta Cloud API direct, ~200 LOC). Goal: cherry-pick what helps, reject what's overhead.

---

## Architectural mismatch — skip these (overhead, not value)

| Their feature | Why it's overhead for you |
|---|---|
| **Wassenger SDK** (paid BSP wrapper) | You use Meta Cloud API directly. Their SDK is a paid abstraction over Meta + a chat ops UI. Switching means a recurring bill + vendor lock-in. Your direct integration is **better and cheaper**. |
| **Ngrok tunneling** | You have a real VPS with nginx. Ngrok is for laptop dev. |
| **Audio output (TTS)** | Hotel guests don't need synthesized voice replies. Adds $0.015/1k chars + latency. |
| **Image input (GPT-4o vision)** | Guests rarely send meaningful images to a hotel bot. Adds ~5× input cost. Maybe useful if you accept passport photos, but that's a v2 conversation with a privacy spec. |
| **Their RAG / functions for CRM/plans/pricing** | Your hotel data fits in a 500-token system prompt. RAG is overkill — adds infra, latency, and a chunking/embedding pipeline for content that's already loaded once. |
| **Labels/metadata via Wassenger** | They piggyback on Wassenger's chat UI. You'd need to build equivalent in Supabase. Possible, but not a fast win. |
| **Whisper agent loop with audio-only mode** | Niche. Skip. |

---

## Worth borrowing — high value, low overhead

### 1. `maxInputCharacters` cap ⭐⭐⭐
**Their:** `limits.maxInputCharacters: 1000`.
**You:** no input limit at all. Someone pastes a 50KB message → you pay for it + risk prompt injection room.
**Cost to add:** 2 lines in `app.py`. Slice `text[:1000]` before passing to bot.

### 2. Function calling for booking intent ⭐⭐⭐
**Their:** `bookSalesMeeting({name, email, datetime})` — OpenAI tool that the model invokes when it has all the slots.
**You:** keyword-based `is_booking_intent()` (already flagged in `improvements.md#7`). The wassenger pattern is the *concrete* implementation of that fix:
```python
tools = [{
  "type": "function",
  "function": {
    "name": "record_booking",
    "parameters": {
      "type": "object",
      "properties": {
        "guest_name": {"type": "string"},
        "check_in": {"type": "string", "format": "date"},
        "check_out": {"type": "string", "format": "date"},
        "num_guests": {"type": "integer"},
      },
      "required": ["guest_name", "check_in", "check_out", "num_guests"],
    },
  },
}]
```
The model only calls `record_booking` when **all four slots** are filled. Your `notify.send_owner_alert` gets called from the tool handler with **structured data** instead of "Хочу забронировать" as raw text. Owner gets a clean alert: *Гость: Айгуль, 5–7 июня, 2 человека.*

### 3. `currentDateAndTime` tool ⭐⭐⭐
**Their:** trivial function that returns `new Date()` for the LLM to consume.
**You:** critical gap. When a guest says "забронировать на завтра", `gpt-4o-mini` has no idea what "завтра" is — its knowledge cutoff is months stale and there's no clock in the prompt. Either:
- Inject `today = 2026-05-27` into the system prompt at every call (1 line, cheap), or
- Expose `current_date()` as a tool.

The system-prompt injection is simpler. Do that.

### 4. `maxMessagesPerChat` + auto-escalation counter ⭐⭐
**Their:** after N messages in 24h, stop replying and hand off.
**You:** no circuit breaker. A confused guest or a bot loop could rack up cost. Add:
```sql
ALTER TABLE conversations ADD COLUMN messages_today INT DEFAULT 0;
ALTER TABLE conversations ADD COLUMN counter_reset_at TIMESTAMPTZ;
```
Above 50/day → reply *"Передаю администратору"* and ping owner. Hard ceiling on cost + on bot embarrassment.

### 5. Skip group chats + archived chats ⭐⭐
**Their:** `skipArchivedChats`, group filter.
**You:** Meta will deliver group messages to your webhook if the number is added to a group. You'd OpenAI-call on every group msg. Check `message["from"]` length / payload shape and skip groups. One `if` statement.

### 6. `maxOutputTokens` ⭐
You already have `max_tokens=400`. Same idea, just calling it out as matching their pattern. Good.

### 7. `chatHistoryLimit` ⭐
You: `CONTEXT_WINDOW=10` + `MAX_HISTORY=20`. They: 20. Same ballpark, no change needed.

---

## Worth borrowing — only if you have time

### 8. Audio input (Whisper transcription)
WhatsApp users in Russia/Kyrgyzstan **do** send voice messages a lot. Whisper-1 is $0.006/min. For a small hotel that's ~$1–2/month even at high volume.

**The pattern:**
```python
if message["type"] == "audio":
    media_url = whatsapp.fetch_media(message["audio"]["id"])
    text = openai.audio.transcriptions.create(model="whisper-1", file=audio_bytes)
    # then route as if it were text
```

Adds ~3s latency. Only do this after fixing the synchronous-webhook defect (`improvements.md` defect #1) — otherwise it'll guarantee Meta timeouts.

### 9. Per-contact metadata for conversation state
They tag contacts (`bot:chatgpt:status`). For you in Supabase:
```sql
ALTER TABLE conversations ADD COLUMN state JSONB DEFAULT '{}';
-- e.g. {"collected": {"name": "Айгуль", "check_in": "2026-06-05"}, "stage": "awaiting_dates"}
```
Useful when you do function calling (#2) — store partial booking state so the model doesn't lose context across the 10-message window.

---

## Recommendation: a "lite port" PR

Don't fork their bot. Cherry-pick the **3 highest-impact ideas** into one PR:

```
feat: borrow wassenger patterns — input cap, current date, booking tool

1. core/bot.py:
   - Cap message_text[:1000] before sending to OpenAI
   - Inject "Сегодня: {today}" into system prompt at runtime
   - Replace keyword is_booking_intent with OpenAI tool `record_booking`
     returning structured slots {name, check_in, check_out, guests}

2. core/notify.py:
   - send_owner_alert now takes the structured booking dict, formats a
     clean Russian message instead of dumping raw user text

3. app.py:
   - Skip group payloads (entry[0].changes[0].value.contacts > 1 etc.)
```

Total diff: ~80 lines. Cost impact: **negative** (input cap + structured intent reduces wasted calls). Latency impact: **none** (tool call is in the same OpenAI round-trip via parallel tools).

### Wins
- Owner alerts become actionable ("Айгуль, 5–7 июня, 2 чел" instead of "хочу бронь")
- "Завтра"/"послезавтра" actually resolve to dates
- Cost ceiling against runaway messages
- Group chats don't burn budget

### What to skip entirely
Wassenger SDK, audio output, image input, RAG, ngrok, their label/metadata system. That's about 70% of their codebase — most of it is wrapper for a paid BSP you don't need.

---

## Priority vs `improvements.md`

`improvements.md` covers **defensive** changes (timeouts, dedup, async webhook, races). Do those **first** — they're ship-blockers.

This document covers **offensive** changes (smarter bot, better UX, structured data). Do these **after** the ship-blockers, in this order:

1. **Input cap** (5 min, do anytime)
2. **Today's date in system prompt** (5 min, immediate UX win)
3. **Function calling for booking** (1–2 hours, biggest product win)
4. **Group chat filter** (5 min)
5. **Daily message ceiling** (30 min)
6. **Audio input** (half-day, only after async webhook is in)
7. **State JSONB column** (half-day, only paired with #3)
