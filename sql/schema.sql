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
  last_alerted_booking_key  TEXT
);

CREATE UNIQUE INDEX conversations_platform_sender_idx
  ON conversations (platform, sender_id);

-- For stale-conversation cleanup queries
CREATE INDEX conversations_updated_at_idx
  ON conversations (updated_at);

-- updated_at is set explicitly by Python on every upsert; no trigger needed.

-- Atomic per-sender daily message counter (avoids read-modify-write race).
CREATE OR REPLACE FUNCTION increment_daily_counter(
  p_platform TEXT,
  p_sender_id TEXT
) RETURNS INT AS $$
DECLARE
  v_count INT;
BEGIN
  UPDATE conversations
    SET messages_today = CASE
          WHEN counter_reset_at IS NULL OR counter_reset_at::DATE < CURRENT_DATE
          THEN 1
          ELSE messages_today + 1
        END,
        counter_reset_at = CASE
          WHEN counter_reset_at IS NULL OR counter_reset_at::DATE < CURRENT_DATE
          THEN CURRENT_TIMESTAMP
          ELSE counter_reset_at
        END
    WHERE platform = p_platform AND sender_id = p_sender_id
    RETURNING messages_today INTO v_count;
  IF NOT FOUND THEN
    v_count := 1;  -- row not yet created; save_history upsert will materialise it
  END IF;
  RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- Dedup table: tracks processed inbound message IDs across all workers.
CREATE TABLE processed_messages (
  message_id   TEXT PRIMARY KEY,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Atomic dedup check-and-insert. Returns TRUE if newly inserted (not a duplicate).
CREATE OR REPLACE FUNCTION mark_message_processed(
  p_message_id TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_inserted INT;
BEGIN
  INSERT INTO processed_messages (message_id)
    VALUES (p_message_id)
    ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  RETURN v_inserted = 1;
END;
$$ LANGUAGE plpgsql;

-- Atomic booking alert dedup. Returns TRUE if alert should fire (booking key changed).
CREATE OR REPLACE FUNCTION set_booking_alert_if_new(
  p_platform TEXT,
  p_sender_id TEXT,
  p_key TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_updated INT;
BEGIN
  UPDATE conversations
    SET last_alerted_booking_key = p_key
    WHERE platform = p_platform AND sender_id = p_sender_id
      AND (last_alerted_booking_key IS DISTINCT FROM p_key);
  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated = 1;
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
      VALUES (p_platform, p_sender_id, p_messages, NOW())
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

-- Migration for existing databases:
-- ALTER TABLE conversations
--   ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--   ADD COLUMN messages_today INT NOT NULL DEFAULT 0,
--   ADD COLUMN counter_reset_at TIMESTAMPTZ,
--   ADD COLUMN last_alerted_booking_key TEXT,
--   ADD CONSTRAINT conversations_platform_check
--     CHECK (platform IN ('whatsapp', 'telegram'));
-- CREATE INDEX conversations_updated_at_idx ON conversations (updated_at);
-- CREATE TABLE processed_messages (
--   message_id TEXT PRIMARY KEY,
--   processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );
-- Then run the RPC creation statements below (copy-paste from this file).
