# Hotel-chat-bot Deep Review v2 (`feat/whatsapp-poc` @ 4981130)

**Scope:** every source + test file, re-read fresh. ~720 lines of code + ~870 lines of tests. 60 tests pass.

Since `improvements.md` was written, 8 commits shipped addressing ~90% of those recommendations. Test count went from 31 → 60. This is the **fresh review** focused on what's still broken, what got fixed badly, and what's newly introduced.

---

## What you nailed since the last review

In commit order: timeouts, env validation, HMAC empty-secret guard, OpenAI client tuning (`timeout=10, max_retries=2`), reply-first ordering with try/except, async webhook ack, message-ID dedup, structured output for booking intent, today's-date injection, group chat filter, daily message ceiling, deep health endpoint, gunicorn `gthread`, nginx 5s timeout, schema CHECK constraint + index, Supabase singleton, system-prompt LRU cache, phone hashing in logs, GH Actions CI, structured logging fields, Meta error JSON check on owner alert.

Of the 20 items in `improvements.md`, **15 are fully closed, 3 are partial, 2 are still open**.

---

## Still broken / newly introduced

### 1. `send_reply` STILL has no error handling — same as before
**`platforms/whatsapp.py:44-57`**. This was `improvements.md` defect #5 and it's the **only ship-blocker that didn't get fixed**.
```python
requests.post(
    f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
    ...
    timeout=(3, 10),
)   # ← no raise_for_status, no return, no log
```
If WhatsApp rejects the reply (24-hour window expired, invalid phone, template required, account flagged), you'll never know. The `_process_whatsapp` caller assumes success. Fix:
```python
resp = requests.post(..., timeout=(3, 10))
if not resp.ok:
    _logger.error("send_reply_failed status=%d body=%s phone=%s", resp.status_code, resp.text[:200], phone_number)
    return False
return True
```

### 2. Dedup is per-process with 4 workers — best-effort 25%
**`platforms/whatsapp.py:7`** + **`deploy/hotel-chat-bot.service:9`** (`--workers 4`).
`_seen_message_ids: set[str]` lives in one Python process. Meta's redelivery can route to any of the 4 workers; the other 3 don't know it was seen. **Effective dedup hit rate ≈ 25%** under normal load balancing.

**Fix:** move to `processed_messages(message_id TEXT PRIMARY KEY, processed_at TIMESTAMPTZ DEFAULT NOW())` in Supabase. Insert with `ON CONFLICT DO NOTHING` and check `rowcount`. Atomic, shared across workers, survives restarts.

### 3. `is_duplicate` calls `.clear()` — catastrophic eviction
**`platforms/whatsapp.py:13-18`:**
```python
if len(_seen_message_ids) >= _SEEN_MAX:
    _seen_message_ids.clear()       # ← drops ALL 512 entries
_seen_message_ids.add(message_id)
```
After 512 distinct messages, the next redelivery walks straight through. A bounded LRU would be 4 lines:
```python
from collections import OrderedDict
_seen: OrderedDict[str, None] = OrderedDict()
def is_duplicate(mid):
    if mid in _seen: _seen.move_to_end(mid); return True
    _seen[mid] = None
    if len(_seen) > 512: _seen.popitem(last=False)
    return False
```
But (#2) means this should be DB-backed anyway.

### 4. **Owner alert spam on every message after booking completes**
**`app.py:86-95`** — fires `send_owner_alert` whenever `_booking_complete(result)` is true. But the **structured output keeps the same 4 slots in subsequent turns** (because conversation history feeds them back to the model). Guest's next message also returns `{guest_name, check_in, check_out, num_guests}` → another alert. And another. And another.

**Reproduction:** guest sends "Айгуль, 5–7 июня, 2 человека" → owner gets alert. Guest sends "Спасибо!" → model still has full context, returns same 4 slots → **owner gets another identical alert**.

**Fix:** persist booking state. Add `bookings JSONB DEFAULT '[]'` to `conversations`, or a `bookings_notified` table. Send alert only when `(guest_name, check_in, check_out, num_guests)` differs from the last alerted tuple for that sender.

### 5. **Escalation alert spam — same pattern, worse**
**`core/bot.py:81-88`** + **`app.py:81-85`**. Once `daily_count > 50`, **every** subsequent message returns `escalated=True` → `send_escalation_alert` fires for messages 51, 52, 53, 54… Owner gets 1 alert per message past the limit. If a user spams 100 messages, owner gets 50 identical pings.

**Fix:** alert only on the **transition** (count crossing the limit), not on every message above it. Track `escalation_notified_at` per conversation and only re-alert after counter reset.

### 6. **`increment_daily_counter` race condition**
**`core/db.py:34-60`** — explicit read-modify-write across two HTTP round-trips:
```python
result = client.table(...).select(...).execute()    # ← read
...
client.table(...).update({"messages_today": new_count}).execute()   # ← write
```
Two concurrent webhooks from the same user → both read 49, both write 50. Counter undercounts; users can blow past the 50/day cap arbitrarily. With 4 workers × 4 threads = 16 concurrent slots, this is realistic.

**Fix:** Supabase RPC (PostgreSQL function) doing atomic `UPDATE conversations SET messages_today = messages_today + 1 ... RETURNING messages_today`. One round-trip, no race.

### 7. **`updated_at` never actually updates** — the new index is useless
**`sql/schema.sql:8`:** `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`. **`core/db.py:66-69`** does an upsert without setting `updated_at`. PostgreSQL `DEFAULT NOW()` only fires on **INSERT**, not on UPDATE via upsert. So `updated_at` permanently reflects the first message ever received from each user.

The `conversations_updated_at_idx` you added at line 18 was explicitly for "stale conversations cleanup" — but it indexes a column that never reflects "most recent activity."

**Fix one of two ways:**

**(a) Trigger:**
```sql
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER conversations_updated_at BEFORE UPDATE ON conversations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

**(b) Pass it from Python in the upsert payload.**

### 8. **Dead `payload` parameter**
**`app.py:74`:** `def _process_whatsapp(payload: dict, phone: str, text: str, message_id: str)` — `payload` is never used inside the function. Drop it.

### 9. **`max_tokens` deprecation**
**`core/bot.py:98`:** `max_tokens=400`. OpenAI SDK has moved to `max_completion_tokens`. `max_tokens` still works but emits `DeprecationWarning`; on reasoning models (o-series) it doesn't behave correctly. Switch the keyword name.

### 10. **`_today()` uses server's local TZ, not hotel's TZ**
**`core/bot.py:66-67`:** `datetime.date.today()` reads the server's local time. If the server is UTC and your hotel is in Bishkek (UTC+6), at 22:00 local Bishkek time `_today()` already says next day's date. Guest "забронировать на завтра" → model picks tomorrow + 1 = wrong date.

**Fix:** `datetime.datetime.now(ZoneInfo(os.environ.get("HOTEL_TZ", "Asia/Bishkek"))).date()`.

### 11. **No validation on booking slots**
`_booking_complete` only checks truthiness. The model could return:
- `check_in: "завтра"` (string, not a date)
- `num_guests: 0` (truthy if 0? no — 0 is falsy, so this slot would be incomplete. But `num_guests: "two"`? truthy.)
- `check_in: "не указано"` (model hedge text)

→ Owner alert fires with garbage data. Validate:
```python
def _booking_complete(r):
    try:
        datetime.date.fromisoformat(r["check_in"])
        datetime.date.fromisoformat(r["check_out"])
        return bool(r.get("guest_name")) and isinstance(r.get("num_guests"), int) and r["num_guests"] > 0
    except (TypeError, ValueError):
        return False
```
The system prompt should explicitly demand ISO-8601 dates.

### 12. **`json.loads` silent failure burns tokens silently**
**`core/bot.py:115-118`:**
```python
try: parsed = json.loads(raw)
except (json.JSONDecodeError, TypeError): parsed = {}
```
On parse failure: user gets generic "Извините…", OpenAI bill is charged, **no log emitted**. With `strict=true` JSON schema this shouldn't happen, but if it does (OpenAI degradation, partial response, content filter), you have no visibility. At minimum:
```python
except (json.JSONDecodeError, TypeError) as e:
    _logger.error("json_parse_failed raw=%r err=%s", raw[:200], e)
```

### 13. **Keyword fallback is now noise, not safety net**
**`core/bot.py:121`:** `booking_intent = parsed.get("is_booking_intent", False) or is_booking_intent(message_text)`. With structured output as the truth source, the OR with keyword matching introduces false positives. Guest asks "Сколько стоит бронь номера?" → "бронь" matches → `booking_intent=True` even though the model correctly said False. Either trust the LLM or trust keywords; don't OR them.

### 14. **`response.json()` in `notify.py` can crash**
**`core/notify.py:35, 63`:** `payload = response.json()`. If Meta returns HTML error page or empty body, `.json()` raises `JSONDecodeError`. Wrap or use `response.json() if 'application/json' in response.headers.get('Content-Type', '') else {}`.

### 15. **Daemon threads die on `systemctl restart`**
**`app.py:120`:** `Thread(target=_process_whatsapp, ..., daemon=True).start()`. Daemon threads are killed when the parent process exits. Gunicorn `--workers 4` graceful reload sends SIGTERM → daemon threads die mid-`bot.handle_message`. If the OpenAI call already completed, you've billed for the message but never sent the reply. No replay.

**Fix:** either (a) drop `daemon=True` and add a signal handler that waits for in-flight threads, or (b) move to a real job queue (RQ + Redis is ~20 lines and survives restarts).

### 16. **No log configuration → logs may not actually emit**
You use `_logger = logging.getLogger(__name__)` everywhere but there's **no `logging.basicConfig()`** call anywhere. Python's default WARNING-level config means your `_logger.info(...)` lines are silently dropped. Gunicorn doesn't auto-configure user loggers. Verify with `journalctl -u hotel-chat-bot -f` — if you see structured logs there, ignore this; if you only see gunicorn access lines, add:
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
```
near the top of `app.py`.

### 17. **`message_id` not validated as non-empty**
**`platforms/whatsapp.py:29`** returns `message["id"]` without checking it. If Meta delivers a payload without `id` (unlikely but possible for some webhook types), `message["id"]` raises `KeyError` → caught at line 30 → returns `None` → message dropped silently. Better: validate explicitly with a useful log.

### 18. **No conversation retention job**
You added the index for stale-conversation cleanup but never wrote the cleanup. Conversations grow forever. For a small hotel, the data volume is tiny — but it's a privacy gap (GDPR/152-ФЗ "data minimization"). Add a 5-line cron that does:
```sql
DELETE FROM conversations WHERE updated_at < NOW() - INTERVAL '90 days';
```

---

## Minor / polish

| # | Where | Issue |
|---|---|---|
| 19 | `app.py:21-23` | `REQUIRED_ENV` check uses `_key not in os.environ`. An env var set to `""` (empty string) passes this check but breaks everything downstream. Use `not os.environ.get(_key)`. |
| 20 | `core/db.py:46` | `if not result.data: return 1` — but no row insert here. Relies on later `save_history` upsert to materialize the row. If `save_history` fails (transient), counter is permanently 0 for that user. Not a hard bug, just fragile. |
| 21 | `core/db.py:48` | `reset_date = (row.get("counter_reset_at") or "")[:10]` — string-comparing ISO dates works but is brittle (assumes "YYYY-MM-DD…" prefix). Parse properly. |
| 22 | `core/bot.py:114` | `raw = response.choices[0].message.content or "{}"` — `or "{}"` covers the None case but the fallback parses to `{}` and silently degrades. Fine but reinforces #12 — needs logging. |
| 23 | `tests/test_app.py:116-123` | `_SyncThread` makes thread-based code synchronous in tests, which is great for assertions but **hides every concurrency bug** in `_process_whatsapp`. Add at least one integration test that uses a real `threading.Thread` with `.join(timeout=)`. |
| 24 | `tests/test_app.py:128` | Each test calls `wa_module._seen_message_ids.clear()` manually as setup. That's a smell — better as a pytest autouse fixture. |
| 25 | `deploy/nginx.conf` | `proxy_read_timeout 5s` — perfect for the new async ack design. But no `limit_req_zone` rate limiting. Add `limit_req_zone $binary_remote_addr zone=webhook:10m rate=10r/s;` to protect against abuse / replay floods. |
| 26 | `deploy/hotel-chat-bot.service` | `Restart=always RestartSec=5` is good. Missing: `LimitNOFILE=65535` (file descriptor limit), `Environment="PYTHONUNBUFFERED=1"` so logs flush. |
| 27 | `.github/workflows/test.yml` | Only runs `pytest -q`. No lint (ruff), no type-check (mypy/pyright), no coverage report. With the typed-ish style you already use, mypy would catch real bugs cheaply. |
| 28 | `requirements.txt` | Pins are inconsistent: `flask==3.0.3`, `openai>=1.30.0` (range), `supabase==2.4.2`, `requests==2.31.0`. The `openai>=` range means CI is non-reproducible. Pin or use `~=`. |
| 29 | `requirements.txt:openai>=1.30.0` | OpenAI 2.x is installed. Range allows 2.x — fine — but pin to a tested version. |
| 30 | System prompt | Still uses `[НАЗВАНИЕ ОТЕЛЯ]` placeholders. Make sure a deployed copy has real values; consider env-var substitution at load time so the file in git stays clean. |

---

## Security delta

| Item | Status since v1 |
|---|---|
| HMAC verification | ✅ done |
| Env validation | ✅ done |
| HMAC empty-secret guard | ✅ done |
| **Owner-alert spam** | 🔴 newly introduced (defect #4) |
| **Escalation-alert spam** | 🔴 newly introduced (defect #5) |
| **PII in conversations table (no retention)** | 🟡 still open |
| **No nginx rate limit** | 🟡 still open |
| **Logs may not flush** | 🟡 newly relevant (defect #16) |
| **No log structured format / message-content redaction** | 🟡 partially open |

---

## Priority order

**Ship-blockers (before public deployment):**
1. Fix `send_reply` error handling (#1) — 5 min
2. Stop owner-alert spam (#4) — 30 min, needs schema column
3. Stop escalation-alert spam (#5) — 20 min
4. Move dedup to Supabase (#2, #3) — 1 hour
5. Verify `_logger` actually emits in prod (#16) — check `journalctl`, 5 min if broken add `basicConfig`

**Same-day fixes:**
6. Race fix on `increment_daily_counter` via RPC (#6) — 30 min
7. `updated_at` trigger or explicit upsert field (#7) — 10 min
8. TZ on `_today()` (#10) — 5 min
9. Booking slot validation (#11) — 15 min
10. Remove dead `payload` param (#8) and `max_tokens` rename (#9) — 5 min

**This week:**
11. Drop daemon threads or add graceful shutdown (#15)
12. Log JSON parse failures (#12)
13. Drop keyword fallback OR or document it (#13)
14. Lint + mypy in CI (#27)
15. Retention cron (#18)
16. nginx rate limit (#25)

**Don't bother right now:** the test ergonomics (#23, #24), pin tightening (#28), service tweaks (#26).

---

## One-line summary

You closed almost everything from v1. The remaining real risks are **two alert-spam bugs (#4, #5)**, **broken multi-worker dedup (#2)**, and **`send_reply` still silently failing (#1)**. Fix those four and you're production-ready.
