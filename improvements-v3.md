# Hotel-chat-bot Review v3 — What's Left After Two Rounds of Fixes

**Scope:** `feat/whatsapp-poc` at commits `2da5f27` + `16552e5`. 66 tests passing. Re-validation of all four prior review docs (`improvements.md`, `proposalstoenhance.md`, `improvements-v2.md`, `wassenger-review.md`) against current code.

The smaller LLM closed almost everything. This doc covers (1) what's still open from prior reviews and (2) new defects introduced by the recent fixes.

---

## Validation pass: all four docs cross-referenced against current code

### `improvements.md` (v1) — 20 items + 5 security

| # | Status | Notes |
|---|---|---|
| 1–5 ship-blockers | ✅ closed | async ack, dedup, timeouts, alert order, send_reply error handling |
| 6 history race | 🔴 **still open** | `get_history` → mutate → `save_history` is still read-modify-write on JSONB |
| 7–17 | ✅ closed | structured output, prompt cache, singleton, schema, env validation, logging, OpenAI tuning, Meta error check, gunicorn, nginx, history slicing |
| 18 owner template message | 🔴 **still open** | After 24h of guest silence Meta rejects freeform replies to the owner number |
| 19–20 | ✅ closed | `/health/deep`, GH Actions CI with ruff |
| Sec 1 retention | 🟡 partial | SQL written, pg_cron schedule is commented-out — needs manual activation |
| Sec 2 verify token | 🟡 still open | `WHATSAPP_VERIFY_TOKEN=hotel-bot-verify-2026` still in `.env.example` |
| Sec 3 HMAC empty | ✅ closed | |
| Sec 4 log redaction | ✅ closed | |
| Sec 5 nginx rate limit | ✅ closed | `limit_req_zone` added |

### `improvements-v2.md` — 30 items

| # | Status |
|---|---|
| 1 send_reply error handling | ✅ closed |
| 2 multi-worker dedup | ✅ closed (Supabase RPC) |
| 3 set.clear() catastrophe | ✅ obsolete (DB-backed) |
| 4 owner alert spam | ✅ closed (`check_and_set_booking_alert`) |
| 5 escalation spam | ✅ closed (`escalated == DAILY_LIMIT+1` transition only) |
| 6 counter race | ✅ closed (PostgreSQL RPC) |
| 7 updated_at | ✅ closed (explicit in upsert) |
| 8 dead `payload` param | ✅ closed |
| 9 max_completion_tokens | ✅ closed |
| 10 TZ | ✅ closed (ZoneInfo) |
| 11 slot validation | ✅ closed (`fromisoformat`) |
| 12 json parse log | ✅ closed |
| 13 keyword OR fallback | ✅ closed |
| 14 notify json crash | ✅ closed |
| 15 daemon threads / graceful shutdown | 🔴 **still open** — `Thread(..., daemon=True)` unchanged |
| 16 logging basicConfig | ✅ closed |
| 17 message_id validation | ✅ closed (commit 16552e5) |
| 18 retention | 🟡 SQL exists, pg_cron schedule commented — not auto-running |
| 19 empty-string env check | ✅ closed |
| 20–22 minor | ✅ closed |
| 23 `_SyncThread` hides races | 🟡 still open (low pri) |
| 24 manual clear in tests | ✅ obsolete |
| 25 nginx rate limit | ✅ closed |
| 26 systemd hardening | ✅ closed |
| 27 mypy in CI | 🟡 ruff added, mypy still missing |
| 28–29 deps pinned | ✅ closed |
| 30 placeholder system prompt | 🟡 still open (intentional) |

### `proposalstoenhance.md`

All 7 priority items shipped.

---

## 🔴 New issues introduced by the recent fixes

### A. `send_reply` return value is ignored by `app.py`
You fixed `send_reply` to return `bool`, but the caller drops it:

```python
# app.py:90
whatsapp.send_reply(phone, result["reply"])   # ← return ignored
```

Consequences:
- Send fails → user gets no reply → bot proceeds to fire owner alert about a "booking" the guest doesn't know was received
- Latency log claims success regardless

**Fix:**
```python
send_ok = whatsapp.send_reply(phone, result["reply"])
if not send_ok:
    _logger.error("reply_undelivered phone=%s message_id=%s", phone_hash, message_id)
    return  # don't fire owner alert if guest didn't get their reply
```

### B. `check_and_set_booking_alert` is itself a read-modify-write race
The fix for v2 #4 introduces a smaller version of the same defect:

```python
# core/db.py:69-82 — SELECT then UPDATE, two round-trips
result = client.table(...).select("last_alerted_booking_key")...execute()
if result.data and result.data[0].get(...) == key: return False
client.table(...).update({"last_alerted_booking_key": key})...execute()
```

Two concurrent webhooks from the same user with the same complete booking → both read `last_alerted_booking_key = None` → both decide "new" → **owner gets 2 alerts**.

**Fix:** turn this into another atomic RPC, same pattern as `mark_message_processed`:
```sql
CREATE OR REPLACE FUNCTION set_booking_alert_if_new(p_platform TEXT, p_sender_id TEXT, p_key TEXT)
RETURNS BOOLEAN AS $$
DECLARE v_updated INT;
BEGIN
  UPDATE conversations SET last_alerted_booking_key = p_key
    WHERE platform = p_platform AND sender_id = p_sender_id
      AND (last_alerted_booking_key IS DISTINCT FROM p_key);
  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql;
```

### C. `is_duplicate_message` is True if `result.data is None`
```python
# core/db.py:60
return not result.data
```
- `True` (newly inserted) → returns `False` (not duplicate) ✓
- `False` (conflict) → returns `True` (duplicate) ✓
- `None` (SDK quirk on empty body or RPC error) → returns `True` (treats as duplicate) → **every message dropped silently**

If the Supabase RPC return shape ever changes or the table is temporarily unreachable, the failure mode is "bot goes dark on every message and you have no idea why." Should be:
```python
return result.data is False  # only treat as dup on explicit conflict
```
And log any other case (None, error).

### D. `BOOKING_KEYWORDS` and `is_booking_intent()` are dead code
v2 #13 removed the OR fallback. The keyword list and function are still exported and tested in `tests/test_bot.py` (4 tests), but never called by `handle_message` anymore. Either:
- Delete it and the 4 dead tests, **or**
- Restore as a safety net when `parsed = {}` (current behavior: total silence on parse failure)

Right now they're export-only — confusing to readers.

### E. No fallback when `parsed = {}`
v2 #12 added a log but didn't add behavioral fallback. If structured output ever fails to parse:
```python
parsed = {}                                   # bot.py:118
reply = parsed.get("reply") or "Извините…"   # generic reply
booking_intent = parsed.get("is_booking_intent", False)  # always False
```
→ User gets generic error, **booking is never detected** for that message. With keywords-OR removed (D), there's no safety net. Either restore the keyword OR as a fallback only when `parsed == {}`, or accept the risk.

### F. `from zoneinfo import ZoneInfo` inside `_today()`
**`core/bot.py:67-68`** — import statement runs on every call (Python caches it but still re-resolves). Trivial perf cost, but a style smell after adding a clean module-level import in every other file. Move to module top.

### G. CI installs ruff inline, no version pin, no config
**`.github/workflows/test.yml`:**
```yaml
pip install ruff           # ← no version
ruff check .               # ← no ruff.toml/pyproject config
```
- Across CI runs, different ruff versions = different lint results
- No way for a developer to reproduce CI locally without guessing
- Defaults can flip between ruff versions

**Fix:** pin in `requirements-dev.txt` (`ruff==0.6.x`), add a minimal `[tool.ruff]` block to `pyproject.toml` or `ruff.toml`, run `ruff check . --output-format=github` for nicer PR annotations.

### H. Retention SQL is not actually running
`sql/retention.sql` is a one-shot DELETE script. The pg_cron schedule is **commented out** in the file header. Until somebody manually runs `SELECT cron.schedule(...)` against the Supabase SQL editor, the retention does nothing and `processed_messages` grows unbounded.

Two options:
1. Add the unschedule + reschedule SQL as live statements (not commented), idempotent.
2. Document explicitly in README "after deploy, run this SQL once in Supabase SQL editor."

### I. CI doesn't run the retention SQL or test the RPCs
`sql/schema.sql` is never executed in CI. If someone breaks an RPC syntax (`increment_daily_counter`, `mark_message_processed`), tests still pass because they mock at the Python boundary. The first failure is in prod.

Cheap fix: add a `postgres:16` service container to GH Actions, `psql -f sql/schema.sql`, then run a tiny Python smoke test that invokes the RPCs. Adds ~20 lines.

---

## 🔴 Still open from v2 (not closed by the recent commits)

### J. Daemon threads die on `systemctl restart` (v2 #15)
`Thread(target=_process_whatsapp, ..., daemon=True).start()` unchanged. Gunicorn rolling reload → SIGTERM to worker → in-flight threads killed mid-OpenAI call. You billed for the message, never sent the reply, never alerted the owner. The `mark_message_processed` row is already committed though — so on **redelivery**, dedup skips it. User is stuck with no reply forever.

Minimal fix:
```python
import signal, threading
_inflight: set[threading.Thread] = set()

def _graceful_exit(signum, frame):
    for t in list(_inflight): t.join(timeout=15)
signal.signal(signal.SIGTERM, _graceful_exit)
```
Real fix: move to RQ or Celery with a Redis backend. 30 lines.

### K. History race (v1 #6)
`get_history` → append → `save_history` is still read-modify-write on `messages JSONB`. Two concurrent messages from same user → second write loses the first's appended turn. Worse on a chat where a user double-taps. Was flagged in v1, never addressed. Either:
- Switch to append-only `messages(id, conversation_id, role, content, ts)` table, **or**
- Atomic RPC: `UPDATE conversations SET messages = messages || $1::jsonb`.

### L. Owner alert outside 24h window (v1 #18)
Meta's customer service window: outside 24 hours since the owner's last inbound message to your business number, **freeform text to the owner is rejected** with error code 131047 / 131051. Both `send_owner_alert` and `send_escalation_alert` use freeform `type: "text"`. After 24h of no booking traffic, the next booking alert silently fails (it'll log a Meta error now, thanks to `raise_for_status` + the JSON check, but the owner never hears).

Fix: register an "owner_booking_alert" template in Meta Business Manager (~24h approval) and switch the payload to:
```python
{
  "messaging_product": "whatsapp",
  "to": owner_number,
  "type": "template",
  "template": {"name": "owner_booking_alert", "language": {"code": "ru"},
               "components": [{"type": "body", "parameters": [
                  {"type": "text", "text": booking["guest_name"]}, ...
               ]}]}
}
```

---

## 🟡 Minor / low priority still open

| Item | Why low |
|---|---|
| mypy in CI | Cheap to add but not blocking |
| `_SyncThread` hides concurrency | Test-only |
| System prompt placeholders | Intentional template |
| Verify token placeholder | Should be rotated in prod regardless |

---

## Final scorecard

| Doc | Items | Closed | Open | Partial |
|---|---|---|---|---|
| improvements.md | 25 | 21 | 2 | 2 |
| proposalstoenhance.md | 7 | 7 | 0 | 0 |
| improvements-v2.md | 30 | 26 | 1 | 3 |
| Newly introduced | 9 | 0 | 9 | 0 |

---

## The three real risks remaining

1. **`send_reply` bool ignored (A)** — 3-line fix, prevents bot from confirming bookings the guest never received
2. **`check_and_set_booking_alert` race (B)** — one atomic SQL RPC, prevents duplicate owner alerts under concurrent load
3. **Daemon threads (J) + 24-hour window (L)** — both architectural; require Meta template approval + a signal handler or job queue

Everything else is cleanup.

---

## Priority order

**Today (each <30 min):**
1. Wire `send_reply` return value into `app.py` (A) — 5 min
2. Atomic RPC for booking alert dedup (B) — 20 min
3. Tighten `is_duplicate_message` None handling (C) — 5 min
4. Delete dead `BOOKING_KEYWORDS` + tests, OR wire as parse-failure fallback (D, E) — 15 min
5. Move `ZoneInfo` import to module top (F) — 2 min
6. Pin ruff + add `ruff.toml` (G) — 10 min
7. Make retention SQL live-runnable or document the manual step (H) — 10 min

**This week:**
8. Postgres service container in CI for RPC smoke tests (I) — 1 hour
9. Graceful-shutdown SIGTERM handler (J) — 30 min, or full job queue migration — half day
10. Atomic history append RPC (K) — 30 min, or schema migration to append-only — 2 hours
11. Owner template message + Meta approval (L) — 30 min code, 24h approval wait
12. Add mypy to CI — 20 min

**Optional polish:**
13. Real-thread integration test
14. README note about manual SQL bootstrap
15. Rotate `WHATSAPP_VERIFY_TOKEN` placeholder
