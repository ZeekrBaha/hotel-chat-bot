CREATE TABLE conversations (
  id            BIGSERIAL PRIMARY KEY,
  platform      TEXT NOT NULL DEFAULT 'whatsapp',
  sender_id     TEXT NOT NULL,
  messages      JSONB NOT NULL DEFAULT '[]',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX conversations_platform_sender_idx
  ON conversations (platform, sender_id);
