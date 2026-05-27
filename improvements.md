# Code Review: hotel-chat-bot (`feat/whatsapp-poc`)

**Scope:** `app.py`, `core/{bot,db,notify}.py`, `platforms/whatsapp.py`, `tests/*`, `deploy/*`, `sql/schema.sql`. 31 tests pass. Repo is roughly 200 LOC of production code — small enough to review every line.

---

## What was done well

| Area | Why it's good |
|---|---|
| **Module layout** | `core/` (business logic) vs `platforms/` (channel adapters) vs `app.py` (HTTP) is a clean separation — Telegram will slot in as a sibling to `platforms/whatsapp.py` without touching `core/`. |
| **HMAC verification** | `verify_signature` uses `hmac.compare_digest` (constant-time) and rejects the missing-prefix case. Textbook-correct. |
| **Lazy OpenAI client** | The `_get_openai_client()` singleton pattern (fixed in `a660dac`) avoids client construction at import time — tests don't need a real key, and prod gets one connection-pooled client. |
| **`None` content guard** | `response.choices[0].message.content or "Извините…"` survives tool-use / refusal responses where `content` is None. |
| **History trimming** | `MAX_HISTORY=20` in `db.save_history` prevents JSONB row bloat; `CONTEXT_WINDOW=10` keeps prompt size bounded. Both costs (storage + tokens) are decoupled. |
| **Webhook hygiene** | 200 on non-text messages and on malformed payloads — Meta won't retry. Verify token path is correct. |
| **Test quality** | 31 tests cover the unit surface, mocks are at the right boundaries (`patch("core.bot.db.get_history")`, not the supabase client internals). `conftest.py` autouses env vars — nice. |
| **Cost-conscious model** | `gpt-4o-mini` + 10-message window + 400-token cap = realistic ~$0.25/mo for a small hotel. |
| **Bilingual prompt** | Strict "detect language → answer only in that language" rule with explicit phrase templates is more robust than letting the model freelance. |

---

## Production defects (fix before going live)

### 1. Webhook is fully synchronous — Meta will retry under load
**`app.py:24-41`** — the webhook does Supabase read → OpenAI call → Supabase write → owner alert → WhatsApp send, all before returning 200. Meta's Cloud API webhook timeout is **20 seconds**, and `deploy/nginx.conf:18` sets `proxy_read_timeout 10s`. OpenAI p99 latency on `gpt-4o-mini` regularly exceeds 5s; combined with two Supabase round-trips and two Graph API calls, a slow request will hit the nginx timeout, Meta will retry, and the user gets a duplicate reply (and you pay for it twice).

**Fix:** Ack 200 immediately, process in background.
```python
from threading import Thread
@app.post("/whatsapp/webhook")
def whatsapp_inbound():
    if not whatsapp.verify_signature(...): return "Unauthorized", 401
    Thread(target=_process, args=(request.json,), daemon=True).start()
    return "", 200
```
For real scale, use Celery/RQ/Cloud Tasks. For POC, threads are fine — but you **must** ack first.

### 2. No message deduplication
Meta will redeliver the same payload on any non-2xx or timeout. Each inbound message has a unique `messages[0].id` (a `wamid.*` string). You're not storing it, so retries → duplicate OpenAI calls + duplicate replies + duplicate owner alerts.

**Fix:** add a `processed_messages(message_id PRIMARY KEY, received_at)` table or a `messages JSONB` field with seen IDs, and short-circuit if already seen. Even a simple in-memory `lru_cache` on `message_id` survives single-worker restarts.

### 3. No timeouts on outbound HTTP — workers can hang forever
**`platforms/whatsapp.py:28`** and **`core/notify.py:20`** both call `requests.post(...)` with **no `timeout=`**. If the Graph API stalls (it does — Facebook has had multi-hour partial outages), the worker thread hangs indefinitely. With `gunicorn --workers 2` (`deploy/hotel-chat-bot.service:10`) you can deadlock the whole service after two stuck requests.

**Fix:** every `requests` call needs `timeout=(connect, read)`, e.g. `timeout=(3, 10)`. This is the single most common production bug in Python web services.

### 4. Owner-alert error blocks the user reply
**`app.py:37-40`** runs `notify.send_owner_alert(...)` *before* `whatsapp.send_reply(...)`. The recent fix (`a660dac`) added `raise_for_status()` in `notify.py`, so if the owner alert API call fails (rate limit, transient 5xx), the user never gets their reply. The order should be: reply first, alert best-effort.
```python
whatsapp.send_reply(phone, reply)
if bot.is_booking_intent(text):
    try: notify.send_owner_alert(...)
    except Exception: logger.exception("owner alert failed")
```

### 5. `send_reply` silently swallows errors
**`platforms/whatsapp.py:28`** — no `raise_for_status()`, no logging. If WhatsApp rejects (e.g., 24-hour messaging window expired, invalid phone number, template required), you'll never know. Log + check status; surface in your monitoring.

### 6. Race condition on conversation history
**`core/bot.py:33-48`** does read-modify-write on `conversations.messages` with no locking. Two messages from the same user arriving simultaneously (whatsapp users do double-tap) → second write clobbers first. With `--workers 2`, this happens across processes too.

**Fix:** Either (a) move to append-only `messages` table (one row per turn), or (b) use Postgres `jsonb_set` server-side in an UPDATE, or (c) use Supabase RPC with a transaction. (a) is the cleanest and also fixes pagination/audit.

### 7. `is_booking_intent` is a brittle keyword list
**`core/bot.py:5-8`** — misses "I'd like to stay 2 nights", "есть свободные с 5 по 8?", "келип конуп кеткибиз бар" (Kyrgyz "we'd like to stay"). Will silently fail on the most important business signal you have.

**Fix:** ask the LLM. Two clean options:
- **Structured output:** add `response_format={"type": "json_schema", ...}` and have the model return `{"reply": "...", "is_booking_intent": bool, "guest_name": "..."}` in one call. Costs nothing extra.
- **Function calling:** declare a `record_booking_intent(...)` tool; the model invokes it when ready. More idiomatic but adds a round-trip.

The keyword list is fine as an *additional* safety net.

---

## Should improve (correctness, robustness, ops)

| # | Location | Issue | Fix |
|---|---|---|---|
| 8 | `core/bot.py:21-24` | `get_system_prompt()` re-reads the file on **every message**. Wasteful syscall + race with editors. | `@functools.lru_cache(maxsize=1)` or read once at module import. Add `SIGHUP` reload if you want hot-edits. |
| 9 | `core/db.py:7-8` | `create_client()` runs on every `get_history` / `save_history` — 2 instantiations per message. | Module-level singleton, same pattern as `_get_openai_client()`. |
| 10 | `sql/schema.sql` | No `CHECK (platform IN ('whatsapp','telegram'))`, no `created_at`, no index on `updated_at`. | Add the CHECK, add `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, index `updated_at` for "stale conversations" cleanup. |
| 11 | `app.py:19, 27` | `os.environ[...]` raises `KeyError` mid-request if env var missing. | Validate at startup: `for k in REQUIRED_ENV: os.environ[k]` at module load — fail fast, not on first webhook. |
| 12 | Everywhere | **No structured logging.** Gunicorn access log only. No request_id, no wamid, no LLM latency, no cost tracking. | `logging.getLogger(__name__)`, log every webhook with `message_id, phone (hashed), latency_ms, model, tokens_in, tokens_out`. |
| 13 | `core/bot.py` | No OpenAI retry/timeout config. SDK defaults to 600s timeout and 2 retries. | `OpenAI(api_key=..., timeout=10.0, max_retries=2)`. |
| 14 | `core/notify.py:30` | `raise_for_status()` after `requests.post` — but Meta returns 200 with `{"error": {...}}` in some failure modes. | Also check `response.json().get("error")`. |
| 15 | `deploy/hotel-chat-bot.service:10` | `--workers 2` sync workers + outgoing HTTP = max ~14 req/min throughput. | `--workers 4 --worker-class gthread --threads 4` or move processing async (defect #1). |
| 16 | `deploy/nginx.conf:18` | `proxy_read_timeout 10s` is too tight given current sync design. | If you implement defect #1's async ack, **5s** is plenty. If not, raise to 25s to outlast OpenAI tail latency. |
| 17 | Tests | `tests/test_bot.py:74-89` asserts `len(sent_messages) == 11` (system + 10) — correct, but doesn't verify which 10 were kept. Could pass with off-by-one. | Add `assert sent_messages[-1]["content"] == "Новое сообщение"` and `sent_messages[1]["content"] == "6"`. |
| 18 | `core/notify.py` | Owner phone is sent as the destination of a freeform WhatsApp message. If the owner hasn't messaged the bot in 24h, Meta will reject the freeform text — owner alerts must use a **template message** outside the session window. | Use a pre-approved template (`POST .../messages` with `type: "template"`). Otherwise alerts will silently fail after 24h of inactivity. |
| 19 | `app.py` | No `/metrics` or healthcheck depth — `/health` only returns `{"status":"ok"}`, doesn't verify Supabase or OpenAI reachable. | Add `/health/deep` that pings both. |
| 20 | Repo | No CI workflow (`.github/workflows/`), no pre-commit, no linter config (ruff/black). Tests only run when you remember to. | Add a 10-line GH Actions workflow: `pytest` on push. Add `ruff` + `mypy` (project is fully typed-ish). |

---

## Security & privacy

1. **PII at rest in Supabase.** Phone numbers + free-form guest messages are stored unencrypted. For a hotel in any jurisdiction with personal-data laws (Russia 152-ФЗ, Kyrgyzstan's PD law, GDPR if any EU guest), this is regulated. At minimum: enable Supabase row-level encryption add-on, document retention, expose a deletion endpoint.
2. **`WHATSAPP_VERIFY_TOKEN=hotel-bot-verify-2026`** is committed in `.env.example`. Looks placeholder-ish but make sure prod's value is different and rotated.
3. **HMAC empty-secret edge case.** If `WHATSAPP_APP_SECRET` env var is set to empty string in prod (deploy mistake), `verify_signature` still produces a valid HMAC and accepts any payload signed with `""`. Add `if not secret: return False`.
4. **Logging risks.** Once you add logging (item #12), do **not** log raw `message_text` — it may contain credit card numbers, passport scans (people send weird things). Hash the phone, redact the body, or log only length.
5. **No rate limiting at nginx.** Health endpoint and webhook are unprotected from volumetric abuse. Add `limit_req_zone` (10 req/s per IP) — won't affect Meta's webhook delivery.

---

## Best-practice references (worth a skim before Task 5)

- **Meta Cloud API webhooks** — `developers.facebook.com/docs/whatsapp/cloud-api/guides/webhooks`. Key bits: 20s timeout, retry semantics, `messages[0].id` for dedup, 24-hour customer service window for free-form replies.
- **OpenAI structured outputs** — `platform.openai.com/docs/guides/structured-outputs`. Lets you replace the keyword-based `is_booking_intent` with a single LLM call that returns both reply + intent + extracted slots (name/dates/guests). Zero extra latency, zero extra cost vs current setup.
- **Supabase Python SDK** — `supabase.com/docs/reference/python`. Specifically the `rpc()` call for atomic upserts to fix the race condition in defect #6.

---

## Priority order (minimal changes first)

**Ship-blockers (do before Task 5 / public webhook):**
1. Add `timeout=(3, 10)` to all `requests.post` calls.
2. Re-order in `app.py`: reply first, owner alert in try/except.
3. Validate env vars at startup.
4. Add message-ID dedup table.

**Next iteration (real load):**
5. Make webhook ack-then-process-async.
6. Switch `is_booking_intent` to LLM structured output.
7. Fix `get_history`/`save_history` race (append-only table).
8. Cache system prompt + Supabase client.

**Ops/quality:**
9. Structured logging with request IDs.
10. GH Actions CI running `pytest`.
11. `/health/deep` and basic metrics.

The codebase is **clean, well-tested, and POC-grade ready**. The defects above are about the gap between "POC that runs the happy path" and "production that survives Meta's retries, OpenAI's tail latency, and two simultaneous guests." Fix #1–#7 and you're solid.
