-- Data-retention cleanup: delete conversations inactive for more than 90 days.
-- Satisfies GDPR / 152-ФЗ data-minimisation requirements.
--
-- This SQL creates an idempotent daily schedule via Supabase pg_cron.
-- If the schedule already exists, it is replaced.

SELECT cron.unschedule('retention-cleanup') WHERE EXISTS (
  SELECT 1 FROM cron.job WHERE jobname = 'retention-cleanup'
);

SELECT cron.schedule(
  'retention-cleanup',
  '0 3 * * *',
  $$
    DELETE FROM conversations WHERE updated_at < NOW() - INTERVAL '90 days';
    -- Queue rows double as the dedup guard, so keep them long enough that Meta
    -- retries can no longer arrive (7 days), then drop terminal jobs + their events.
    DELETE FROM message_jobs
      WHERE status IN ('replied', 'dead')
        AND updated_at < NOW() - INTERVAL '7 days';
    DELETE FROM message_events WHERE created_at < NOW() - INTERVAL '30 days';
  $$
);
