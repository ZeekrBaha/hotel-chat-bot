-- ============================================================================
-- Hotel chat bot — database schema
--
-- Two concerns live here:
--   1. conversations   — per-guest chat history + daily rate-limit counter
--   2. durable message queue (message_jobs + message_events)
--
-- The queue replaces the old "processed_messages" dedup table. Inbound webhooks
-- only ENQUEUE; a separate worker process (python -m core.worker) drains the
-- queue, so a failed OpenAI/WhatsApp/Supabase call retries instead of silently
-- dropping the guest's message.
-- ============================================================================

CREATE TABLE conversations (
  id                        BIGSERIAL PRIMARY KEY,
  platform                  TEXT NOT NULL DEFAULT 'whatsapp'
                              CHECK (platform IN ('whatsapp', 'telegram')),
  sender_id                 TEXT NOT NULL,
  messages                  JSONB NOT NULL DEFAULT '[]',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  messages_today            INT NOT NULL DEFAULT 0,
  counter_reset_at          TIMESTAMPTZ,
  last_alerted_booking_key  TEXT,
  -- Owner-alert claim state for the booking key above: NULL / 'sending' / 'sent'
  -- / 'failed'. Lets exactly one worker claim the alert atomically (see
  -- claim_booking_alert) while still allowing a failed send to be retried.
  booking_alert_status      TEXT
                              CHECK (booking_alert_status IN ('sending', 'sent', 'failed')),
  booking_alert_at          TIMESTAMPTZ
);

CREATE UNIQUE INDEX conversations_platform_sender_idx
  ON conversations (platform, sender_id);

-- For stale-conversation cleanup queries
CREATE INDEX conversations_updated_at_idx
  ON conversations (updated_at);

-- updated_at is set explicitly by Python on every upsert; no trigger needed.

-- Atomic per-sender daily message counter (avoids read-modify-write race).
-- Creates the conversation row on the first message with messages_today = 1, so
-- the very first message of the day is actually counted (the old version returned
-- 1 without persisting it, undercounting until append_conversation_turn ran).
CREATE OR REPLACE FUNCTION increment_daily_counter(
  p_platform TEXT,
  p_sender_id TEXT
) RETURNS INT AS $$
DECLARE
  v_count INT;
BEGIN
  INSERT INTO conversations (platform, sender_id, messages, messages_today, counter_reset_at)
    VALUES (p_platform, p_sender_id, '[]'::jsonb, 1, CURRENT_TIMESTAMP)
  ON CONFLICT (platform, sender_id) DO UPDATE
    SET messages_today = CASE
          WHEN conversations.counter_reset_at IS NULL
               OR conversations.counter_reset_at::DATE < CURRENT_DATE
          THEN 1
          ELSE conversations.messages_today + 1
        END,
        counter_reset_at = CASE
          WHEN conversations.counter_reset_at IS NULL
               OR conversations.counter_reset_at::DATE < CURRENT_DATE
          THEN CURRENT_TIMESTAMP
          ELSE conversations.counter_reset_at
        END
    RETURNING messages_today INTO v_count;
  RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- Atomically claim the owner alert for a booking key. Returns TRUE to EXACTLY
-- ONE caller — the single UPDATE takes the row lock, so two parallel jobs for the
-- same completed booking cannot both claim it (no duplicate owner alerts).
--
-- A claim succeeds when the alert is genuinely outstanding:
--   * a brand-new booking key, OR
--   * the previous attempt for this key 'failed' (retry, not suppression), OR
--   * a 'sending' claim went stale (the claiming worker died).
-- It does NOT succeed when the key is already 'sent', or while another worker
-- holds a fresh 'sending' claim. Call finish_booking_alert after the send.
CREATE OR REPLACE FUNCTION claim_booking_alert(
  p_platform TEXT,
  p_sender_id TEXT,
  p_key TEXT,
  p_stale_seconds INT DEFAULT 120
) RETURNS BOOLEAN AS $$
DECLARE
  v_claimed INT;
BEGIN
  UPDATE conversations
    SET last_alerted_booking_key = p_key,
        booking_alert_status = 'sending',
        booking_alert_at = NOW()
    WHERE platform = p_platform AND sender_id = p_sender_id
      AND (
        last_alerted_booking_key IS DISTINCT FROM p_key
        OR booking_alert_status = 'failed'
        OR booking_alert_status IS NULL
        OR (booking_alert_status = 'sending'
            AND booking_alert_at < NOW() - make_interval(secs => p_stale_seconds))
      );
  GET DIAGNOSTICS v_claimed = ROW_COUNT;
  RETURN v_claimed = 1;
END;
$$ LANGUAGE plpgsql;

-- Finish a claimed owner alert: 'sent' (terminal) on success, 'failed'
-- (re-claimable) on failure. Scoped to the current booking key so a newer
-- booking's claim is never clobbered.
CREATE OR REPLACE FUNCTION finish_booking_alert(
  p_platform TEXT,
  p_sender_id TEXT,
  p_key TEXT,
  p_success BOOLEAN
) RETURNS VOID AS $$
BEGIN
  UPDATE conversations
    SET booking_alert_status = CASE WHEN p_success THEN 'sent' ELSE 'failed' END,
        booking_alert_at = NOW()
    WHERE platform = p_platform AND sender_id = p_sender_id
      AND last_alerted_booking_key = p_key;
END;
$$ LANGUAGE plpgsql;

-- Atomic message append. Appends new messages and keeps only the last p_max_history.
CREATE OR REPLACE FUNCTION append_conversation_turn(
  p_platform TEXT,
  p_sender_id TEXT,
  p_messages JSONB,
  p_max_history INT DEFAULT 20
) RETURNS VOID AS $$
BEGIN
  UPDATE conversations
    SET messages = (
      WITH combined AS (
        SELECT elem, row_number() OVER () AS rn
        FROM jsonb_array_elements(
          COALESCE(messages, '[]'::jsonb) || p_messages
        ) AS elem
      ),
      total AS (
        SELECT count(*) AS n FROM combined
      )
      SELECT jsonb_agg(elem ORDER BY rn)
      FROM combined, total
      WHERE rn > GREATEST(0, total.n - p_max_history)
    ),
    updated_at = NOW()
    WHERE platform = p_platform AND sender_id = p_sender_id;
  IF NOT FOUND THEN
    INSERT INTO conversations (platform, sender_id, messages, updated_at)
      VALUES (p_platform, p_sender_id, (
        WITH combined AS (
          SELECT elem, row_number() OVER () AS rn
          FROM jsonb_array_elements(p_messages) AS elem
        ),
        total AS (
          SELECT count(*) AS n FROM combined
        )
        SELECT COALESCE(jsonb_agg(elem ORDER BY rn), '[]'::jsonb)
        FROM combined, total
        WHERE rn > GREATEST(0, total.n - p_max_history)
      ), NOW())
      ON CONFLICT (platform, sender_id) DO UPDATE
      SET messages = (
        WITH combined AS (
          SELECT elem, row_number() OVER () AS rn
          FROM jsonb_array_elements(
            COALESCE(conversations.messages, '[]'::jsonb) || EXCLUDED.messages
          ) AS elem
        ),
        total AS (
          SELECT count(*) AS n FROM combined
        )
        SELECT jsonb_agg(elem ORDER BY rn)
        FROM combined, total
        WHERE rn > GREATEST(0, total.n - p_max_history)
      ),
      updated_at = NOW();
  END IF;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Durable message queue
-- ============================================================================

-- One row per inbound message. message_id is the WhatsApp wamid, so the PRIMARY
-- KEY doubles as the dedup guard (enqueue_message uses ON CONFLICT DO NOTHING).
--
-- Lifecycle:
--   received   -> just enqueued, waiting for a worker
--   processing -> claimed by a worker
--   replied    -> terminal success (reply delivered to the guest)
--   failed     -> transient failure, will be retried (retry_count < max_retries)
--   dead       -> terminal failure / dead-letter (retries exhausted)
--
-- result holds the generated bot reply + booking metadata so a retry re-sends the
-- same reply instead of re-calling OpenAI (cheaper + no double counting).
CREATE TABLE message_jobs (
  message_id   TEXT PRIMARY KEY,
  platform     TEXT NOT NULL,
  sender_id    TEXT NOT NULL,
  text         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'received'
                 CHECK (status IN ('received', 'processing', 'replied', 'failed', 'dead')),
  retry_count  INT  NOT NULL DEFAULT 0,
  max_retries  INT  NOT NULL DEFAULT 3,
  last_error   TEXT,
  result       JSONB,
  -- reply_sent records that the outbound WhatsApp reply was confirmed delivered.
  -- A reclaimed (stale) job with reply_sent = TRUE is NOT re-sent, which keeps a
  -- worker crash *after* a successful send from double-messaging the guest.
  reply_sent   BOOLEAN NOT NULL DEFAULT FALSE,
  sent_at      TIMESTAMPTZ,
  -- history_appended guards the post-send history write, so a reclaim that runs
  -- after a successful append (but a failed succeed_message_job) does not append
  -- the same conversation turn twice.
  history_appended BOOLEAN NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  claimed_at   TIMESTAMPTZ,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports the claim query's ORDER BY created_at over claimable rows.
CREATE INDEX message_jobs_status_created_idx
  ON message_jobs (status, created_at);

-- Append-only audit trail: inbound, reply_generated, send_result, notify_result.
CREATE TABLE message_events (
  id          BIGSERIAL PRIMARY KEY,
  message_id  TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  detail      JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX message_events_message_id_idx
  ON message_events (message_id, created_at);

-- Enqueue an inbound message. Returns TRUE if newly enqueued, FALSE if it is a
-- duplicate (Meta retry of a message we already accepted).
CREATE OR REPLACE FUNCTION enqueue_message(
  p_message_id TEXT,
  p_platform TEXT,
  p_sender_id TEXT,
  p_text TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_inserted INT;
BEGIN
  INSERT INTO message_jobs (message_id, platform, sender_id, text)
    VALUES (p_message_id, p_platform, p_sender_id, p_text)
    ON CONFLICT (message_id) DO NOTHING;
  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  RETURN v_inserted = 1;
END;
$$ LANGUAGE plpgsql;

-- Atomically claim the next workable job. Picks newly received jobs, failed jobs
-- past their retry backoff, and stale 'processing' jobs (whose worker died).
-- FOR UPDATE SKIP LOCKED lets multiple workers run concurrently without contention.
-- Returns 0 rows when the queue is empty.
CREATE OR REPLACE FUNCTION claim_message_job(
  p_stale_seconds INT DEFAULT 120,
  p_retry_backoff_seconds INT DEFAULT 30
) RETURNS SETOF message_jobs AS $$
BEGIN
  RETURN QUERY
  UPDATE message_jobs
    SET status = 'processing', claimed_at = NOW(), updated_at = NOW()
    WHERE message_id = (
      SELECT message_id FROM message_jobs
        WHERE status = 'received'
           OR (status = 'failed'
               AND retry_count < max_retries
               AND updated_at < NOW() - make_interval(secs => p_retry_backoff_seconds))
           OR (status = 'processing'
               AND claimed_at < NOW() - make_interval(secs => p_stale_seconds))
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING *;
END;
$$ LANGUAGE plpgsql;

-- Persist the generated bot result so retries re-send the same reply.
CREATE OR REPLACE FUNCTION save_job_result(
  p_message_id TEXT,
  p_result JSONB
) RETURNS VOID AS $$
BEGIN
  UPDATE message_jobs
    SET result = p_result, updated_at = NOW()
    WHERE message_id = p_message_id;
END;
$$ LANGUAGE plpgsql;

-- Record that the outbound reply was confirmed delivered. Called immediately
-- after a successful send, before the best-effort post-send work, so a later
-- reclaim of this job can skip re-sending the reply.
CREATE OR REPLACE FUNCTION mark_reply_sent(
  p_message_id TEXT
) RETURNS VOID AS $$
BEGIN
  UPDATE message_jobs
    SET reply_sent = TRUE, sent_at = NOW(), updated_at = NOW()
    WHERE message_id = p_message_id;
END;
$$ LANGUAGE plpgsql;

-- Record that the conversation turn was appended, so a post-send reclaim does
-- not append it again.
CREATE OR REPLACE FUNCTION mark_history_appended(
  p_message_id TEXT
) RETURNS VOID AS $$
BEGIN
  UPDATE message_jobs
    SET history_appended = TRUE, updated_at = NOW()
    WHERE message_id = p_message_id;
END;
$$ LANGUAGE plpgsql;

-- Mark a job as successfully replied (terminal).
CREATE OR REPLACE FUNCTION succeed_message_job(
  p_message_id TEXT
) RETURNS VOID AS $$
BEGIN
  UPDATE message_jobs
    SET status = 'replied', updated_at = NOW()
    WHERE message_id = p_message_id;
END;
$$ LANGUAGE plpgsql;

-- Record a failure. Increments retry_count and moves the job to 'failed' (will be
-- retried) or 'dead' (dead-letter) once retries are exhausted. Returns the new status.
CREATE OR REPLACE FUNCTION fail_message_job(
  p_message_id TEXT,
  p_error TEXT
) RETURNS TEXT AS $$
DECLARE
  v_rc INT;
  v_max INT;
  v_new_status TEXT;
BEGIN
  SELECT retry_count, max_retries INTO v_rc, v_max
    FROM message_jobs WHERE message_id = p_message_id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  v_rc := v_rc + 1;
  IF v_rc >= v_max THEN
    v_new_status := 'dead';
  ELSE
    v_new_status := 'failed';
  END IF;
  UPDATE message_jobs
    SET status = v_new_status,
        retry_count = v_rc,
        last_error = left(p_error, 2000),
        claimed_at = NULL,
        updated_at = NOW()
    WHERE message_id = p_message_id;
  RETURN v_new_status;
END;
$$ LANGUAGE plpgsql;

-- Append an audit event for a message.
CREATE OR REPLACE FUNCTION record_message_event(
  p_message_id TEXT,
  p_event_type TEXT,
  p_detail JSONB DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
  INSERT INTO message_events (message_id, event_type, detail)
    VALUES (p_message_id, p_event_type, p_detail);
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Operational monitoring views
-- ============================================================================

-- Job counts per status + oldest/newest in each state. Quick queue overview:
--   SELECT * FROM queue_status;
CREATE OR REPLACE VIEW queue_status AS
SELECT
  status,
  count(*)        AS jobs,
  min(created_at) AS oldest,
  max(updated_at) AS latest
FROM message_jobs
GROUP BY status;

-- Dead-lettered jobs (retries exhausted) — these need a human.
--   SELECT * FROM dead_letter_jobs;
CREATE OR REPLACE VIEW dead_letter_jobs AS
SELECT message_id, platform, sender_id, retry_count, last_error, reply_sent, updated_at
FROM message_jobs
WHERE status = 'dead'
ORDER BY updated_at DESC;

-- Jobs stuck 'processing' for over 5 minutes (a worker likely died). The claim
-- function reclaims these automatically; this view surfaces them for alerting.
--   SELECT * FROM stuck_jobs;
CREATE OR REPLACE VIEW stuck_jobs AS
SELECT message_id, platform, sender_id, retry_count, reply_sent, claimed_at, updated_at
FROM message_jobs
WHERE status = 'processing'
  AND claimed_at < NOW() - INTERVAL '5 minutes'
ORDER BY claimed_at;

-- ============================================================================
-- Migration for existing databases (pre-queue):
--   -- drop the old dedup table; message_jobs replaces it
--   DROP FUNCTION IF EXISTS mark_message_processed(TEXT);
--   DROP TABLE IF EXISTS processed_messages;
--   -- recreate increment_daily_counter (copy from above) to fix the undercount
--   -- then create message_jobs, message_events, and the queue functions above.
-- ============================================================================
