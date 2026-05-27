CREATE TABLE conversations (
  id            BIGSERIAL PRIMARY KEY,
  platform      TEXT NOT NULL DEFAULT 'whatsapp'
                  CHECK (platform IN ('whatsapp', 'telegram')),
  sender_id     TEXT NOT NULL,
  messages      JSONB NOT NULL DEFAULT '[]',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX conversations_platform_sender_idx
  ON conversations (platform, sender_id);

-- For stale-conversation cleanup queries
CREATE INDEX conversations_updated_at_idx
  ON conversations (updated_at);

-- Migration for existing databases:
-- ALTER TABLE conversations
--   ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--   ADD CONSTRAINT conversations_platform_check
--     CHECK (platform IN ('whatsapp', 'telegram'));
-- CREATE INDEX conversations_updated_at_idx ON conversations (updated_at);
