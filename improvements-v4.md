# Hotel-chat-bot Review v4 — Validation after `c8fd7a2`

**Scope:** `feat/whatsapp-poc` at `c8fd7a2 fix: implement improvements-v3 high-priority fixes (A-K)`. 66 tests passing. Re-validation of every item in `improvements-v3.md` plus a fresh read of all changed files.

The smaller LLM closed almost all of v3. This pass found **one critical bug introduced by the K fix** that hides because tests mock the RPC, plus several smaller issues.

---

## Validation: items claimed fixed in `c8fd7a2`

| v3 item | Verdict |
|---|---|
| **A** `send_reply` bool wired | ✅ `app.py:110-113` — captures `send_ok`, returns early if false |
| **B** `set_booking_alert_if_new` RPC | ✅ Atomic UPDATE WHERE distinct in `schema.sql:73-88` |
| **C** `is_duplicate_message` None guard | ✅ Only returns True on `is False`; logs warning otherwise |
| **D+E** keyword fallback | ✅ `bot.py:122-126` — falls back to `is_booking_intent` only when `parsed == {}` |
| **F** ZoneInfo import | ✅ Module top |
| **G** ruff pinned + config | ✅ `ruff.toml`, `requirements-dev.txt: ruff==0.15.14`, `--output-format=github` |
| **H** retention live-runnable | ✅ Idempotent unschedule + reschedule |
| **J** SIGTERM handler | 🟡 Logic right, but **doesn't actually shut down** (see #5) |
| **K** atomic history append | 🔴 **Has a critical bug — see #1** |
| **I** Postgres in CI | ❌ Not in this commit; still open |
| **L** owner template message | ❌ Not in this commit; still open |

---

## 🔴 New defects introduced or missed

### 1. CRITICAL: `append_conversation_turn` keeps the OLDEST messages, not newest

**`sql/schema.sql:99-107`:**
```sql
SET messages = (
  SELECT jsonb_agg(elem)
  FROM jsonb_array_elements(
    CASE WHEN messages IS NULL OR jsonb_array_length(messages) = 0
      THEN p_messages
      ELSE messages || p_messages
    END
  ) AS elem
  LIMIT p_max_history
),
```

PostgreSQL semantics: `LIMIT` applies to the rows produced by `jsonb_array_elements`, in array order, **before** `jsonb_agg` runs. So when the concatenated array has 25 elements, the LIMIT keeps the **first 20** and drops elements 21–25 — i.e. the **most recent messages are silently discarded** and the bot retains the oldest 20 turns forever.

This is the exact opposite of the Python implementation it replaced (`messages[-MAX_HISTORY:]` keeps the LAST 20).

**Why it didn't show up in tests:** `tests/test_db.py` and `tests/test_bot.py` mock the RPC at the Python boundary. The actual SQL is never executed in CI. Same root cause as v3 item I.

**How it manifests in prod:** Bot context degrades after ~20 turns per user. Recent conversation history disappears; ancient history persists. Bot starts "forgetting" what was just discussed and remembering ancient turns. Subtle, slow, and looks like a model bug, not a DB bug.

**Fix:**
```sql
SET messages = (
  WITH combined AS (
    SELECT elem, row_number() OVER () AS rn
    FROM jsonb_array_elements(
      COALESCE(messages, '[]'::jsonb) || p_messages
    ) AS elem
  ),
  total AS (SELECT count(*) AS n FROM combined)
  SELECT jsonb_agg(elem ORDER BY rn)
  FROM combined, total
  WHERE rn > GREATEST(0, total.n - p_max_history)
),
```

**This is a ship-blocker. Fix before deployment.**

### 2. First-message race in `append_conversation_turn` — message lost

**`schema.sql:111-115`:**
```sql
IF NOT FOUND THEN
  INSERT INTO conversations (...) VALUES (...)
    ON CONFLICT DO NOTHING;
END IF;
```

Scenario: brand-new user, two concurrent webhooks (e.g., user double-taps, or sends two messages in quick succession that both arrive within ms):
- W1: UPDATE finds no row → IF NOT FOUND → INSERT succeeds with `[user1, assistant1]`
- W2: UPDATE finds no row → IF NOT FOUND → INSERT ON CONFLICT DO NOTHING → **silently drops W2's turn**

The `IF NOT FOUND` branch needs to **retry the UPDATE** when the INSERT hits a conflict. Cleanest: replace the whole UPDATE/IF NOT FOUND/INSERT dance with a single `INSERT ... ON CONFLICT (platform, sender_id) DO UPDATE SET messages = (... append/trim ...), updated_at = NOW()`.

### 3. `save_history` is now dead code

**`core/db.py:42-52`** is defined, exported, and tested (2 tests in `test_db.py`) but **never called by production code** since K replaced it with `append_conversation_turn`. Confirmed via grep — only callers are tests.

Delete `save_history` and its two tests, or document why it stays.

### 4. Two RPC round-trips per inbound message

**`core/bot.py:128-129`:**
```python
db.append_conversation_turn(platform, sender_id, {"role": "user", ...})
db.append_conversation_turn(platform, sender_id, {"role": "assistant", ...})
```

Each RPC is a separate HTTP round-trip to Supabase (~50–200ms). The RPC already accepts `p_messages JSONB` as an array — pass both in one call:
```python
db.append_conversation_turn(platform, sender_id, [
    {"role": "user", "content": message_text},
    {"role": "assistant", "content": reply},
])
```
Halves latency on this path.

### 5. SIGTERM handler doesn't actually trigger shutdown

**`app.py:40-49`:**
```python
def _graceful_exit(signum, frame):
    ...
    for t in inflight_copy:
        t.join(timeout=15)
    _logger.info("graceful_exit complete")
signal.signal(signal.SIGTERM, _graceful_exit)
```

The handler **overrides** gunicorn's worker SIGTERM handler. Our handler joins threads, logs "graceful_exit complete," and **returns**. Gunicorn's worker doesn't know to stop accepting new connections or exit. The worker just keeps running until gunicorn's `--graceful-timeout` (default 30s) elapses, then gets SIGKILL.

Effects:
- Workers don't actually stop on systemd reload — they wait for SIGKILL.
- New connections accepted during the join window get processed.
- If join takes longer than gunicorn's 30s graceful timeout, SIGKILL hits anyway, killing the threads we were trying to protect.

**Fix:** chain to the original handler OR exit explicitly:
```python
_orig_sigterm = signal.getsignal(signal.SIGTERM)
def _graceful_exit(signum, frame):
    ...
    for t in inflight_copy: t.join(timeout=15)
    if callable(_orig_sigterm):
        _orig_sigterm(signum, frame)   # let gunicorn shut down properly
```
And bump `--graceful-timeout 60` in the systemd unit so the chained handler has room.

### 6. Thread-registration window before `_inflight.add`

**`app.py:158-159`** creates and starts the thread; **`_process_whatsapp:105-107`** adds itself to `_inflight` from inside the thread. There's a microsecond window between `.start()` and the thread reaching line 105 where SIGTERM can hit and the new thread is invisible to the handler.

**Fix:** register before `.start()`:
```python
t = Thread(target=_process_whatsapp, args=(...), daemon=False)
with _inflight_lock: _inflight.add(t)
t.start()
```
And keep the discard in the thread's `finally`.

### 7. `logging.basicConfig` may be a no-op under gunicorn

**`app.py:13-16`** runs `basicConfig` at module import. Gunicorn configures the root logger before importing the app, so the root logger **already has handlers**. `basicConfig` is documented as a no-op when handlers exist. Your `%(asctime)s %(levelname)s %(name)s %(message)s` format may not be applied; logs appear with gunicorn's default format instead.

**Fix:**
```python
logging.basicConfig(level=logging.INFO, format="...", force=True)
```

### 8. Migration comments lag the new schema

**`schema.sql:119-131`** is the migration block for existing DBs. It only covers `created_at`, `messages_today`, `counter_reset_at`, `last_alerted_booking_key`, the platform CHECK, the index, and `processed_messages`.

**Missing from migration block:**
- `CREATE OR REPLACE FUNCTION set_booking_alert_if_new(...)`
- `CREATE OR REPLACE FUNCTION append_conversation_turn(...)`

Anyone running the migration on an older DB ends up with the columns but missing RPCs → `db.check_and_set_booking_alert` and `db.append_conversation_turn` both fail in prod with "function does not exist."

---

## 🔴 Still open from v3

| Item | Status |
|---|---|
| **I** Postgres service container in CI for RPC smoke tests | Open — this is why #1 (LIMIT bug) didn't get caught |
| **L** Owner template message for 24h window | Open — silent failures coming after 24h of guest silence |

---

## 🟡 Lower priority still open

- mypy in CI (v2 #27)
- `_SyncThread` hides concurrency bugs (v2 #23)
- System prompt placeholders (v2 #30)
- `WHATSAPP_VERIFY_TOKEN` value still in `.env.example` (v1 sec #2)

---

## Scorecard

| Doc | Items | Closed | Open | Partial |
|---|---|---|---|---|
| improvements.md (v1) | 25 | 22 | 1 (L) | 2 |
| proposalstoenhance.md | 7 | 7 | 0 | 0 |
| improvements-v2.md | 30 | 28 | 0 | 2 |
| improvements-v3.md | 12 | 9 | 2 (I, L) | 1 (J partial) |
| **New in c8fd7a2** | 8 | 0 | 8 | 0 |

---

## Real risks remaining (priority order)

**Must fix before public deploy:**

1. **`append_conversation_turn` keeps wrong end of history (#1)** — silent prod bug, drops new messages after turn 20. **5-min SQL fix.**
2. **First-message race drops a turn (#2)** — convert to single `INSERT ... ON CONFLICT DO UPDATE`. **10-min SQL fix.**
3. **SIGTERM handler doesn't shut down (#5)** — chain to gunicorn's original handler. **5-min Python fix.**
4. **Schema migration block missing new RPCs (#8)** — anyone migrating from old schema breaks in prod. **2-min docs fix.**

**Same-day:**

5. Combine the two `append_conversation_turn` calls into one (#4).
6. Register thread before start (#6).
7. `force=True` on `basicConfig` (#7).
8. Delete dead `save_history` (#3).

**Still owed from v3:**

9. **I** — Postgres service container in CI. **This is the one that would have caught #1, #2, and #8.** Worth doing now before the next round of SQL changes.
10. **L** — Owner template message. 24h ticking clock on freeform alert failures.

The codebase is excellent quality. **The one production-critical issue is #1** — once that's fixed plus #2 and #5, you're genuinely production-ready.
