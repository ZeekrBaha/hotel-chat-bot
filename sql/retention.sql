-- Data-retention cleanup: delete conversations inactive for more than 90 days.
-- Satisfies GDPR / 152-ФЗ data-minimisation requirements.
--
-- Option A — run once manually or via your deploy pipeline:
--   psql $DATABASE_URL -f sql/retention.sql
--
-- Option B — schedule via Supabase pg_cron (runs daily at 03:00 UTC):
--   SELECT cron.schedule(
--     'retention-cleanup',
--     '0 3 * * *',
--     $$
--       DELETE FROM conversations WHERE updated_at < NOW() - INTERVAL '90 days';
--       DELETE FROM processed_messages WHERE processed_at < NOW() - INTERVAL '7 days';
--     $$
--   );
--
-- To remove the schedule:
--   SELECT cron.unschedule('retention-cleanup');

DELETE FROM conversations
  WHERE updated_at < NOW() - INTERVAL '90 days';

DELETE FROM processed_messages
  WHERE processed_at < NOW() - INTERVAL '7 days';
