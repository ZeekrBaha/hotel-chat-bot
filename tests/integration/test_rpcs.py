"""Integration tests for the plpgsql RPCs against a REAL Postgres.

Unit tests mock the Supabase client, so SQL bugs (like the daily-counter
undercount) can survive them. These run the actual schema.sql functions.

They are skipped unless DATABASE_URL points at a reachable Postgres. CI starts a
Postgres service container and sets it; locally:

    docker run -d --rm -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest tests/integration -q
"""
import json
import os
import pathlib

import pytest

psycopg = pytest.importorskip("psycopg")

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; integration tests require a Postgres container",
)

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


@pytest.fixture
def conn():
    """A connection with a fresh schema loaded from schema.sql per test."""
    c = psycopg.connect(DATABASE_URL, autocommit=True)
    c.execute("DROP SCHEMA IF EXISTS itest CASCADE")
    c.execute("CREATE SCHEMA itest")
    c.execute("SET search_path TO itest")
    c.execute(SCHEMA_PATH.read_text())
    try:
        yield c
    finally:
        c.execute("DROP SCHEMA IF EXISTS itest CASCADE")
        c.close()


def _scalar(conn, sql, params=None):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


# --- increment_daily_counter (the undercount bug) ---

def test_increment_daily_counter_creates_row_with_one(conn):
    count = _scalar(conn, "SELECT increment_daily_counter('whatsapp', '79991234567')")
    assert count == 1
    # The row must actually exist and persist messages_today = 1.
    stored = _scalar(
        conn,
        "SELECT messages_today FROM conversations WHERE platform='whatsapp' AND sender_id='79991234567'",
    )
    assert stored == 1


def test_increment_daily_counter_increments_existing(conn):
    _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")
    second = _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")
    assert second == 2


def test_increment_daily_counter_resets_on_new_day(conn):
    _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")
    conn.execute(
        "UPDATE conversations SET counter_reset_at = NOW() - INTERVAL '2 days' "
        "WHERE platform='whatsapp' AND sender_id='a'"
    )
    again = _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")
    assert again == 1


# --- enqueue / dedup ---

def test_enqueue_message_dedups(conn):
    first = _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    second = _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    assert first is True
    assert second is False


# --- claim / succeed / fail / dead-letter ---

def test_claim_then_succeed(conn):
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    status = _scalar(conn, "SELECT status FROM claim_message_job()")
    assert status == "processing"
    conn.execute("SELECT succeed_message_job('wamid.1')")
    final = _scalar(conn, "SELECT status FROM message_jobs WHERE message_id='wamid.1'")
    assert final == "replied"


def test_claim_returns_no_row_when_empty(conn):
    cur = conn.execute("SELECT status FROM claim_message_job()")
    assert cur.fetchone() is None


def test_mark_reply_sent_sets_flag_and_claim_returns_it(conn):
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    conn.execute("SELECT claim_message_job()")
    conn.execute("SELECT mark_reply_sent('wamid.1')")
    sent = _scalar(conn, "SELECT reply_sent FROM message_jobs WHERE message_id='wamid.1'")
    assert sent is True
    # A reclaimed stale job exposes reply_sent so the worker can skip the resend.
    conn.execute("UPDATE message_jobs SET claimed_at = NOW() - INTERVAL '5 minutes'")
    reclaimed = _scalar(conn, "SELECT reply_sent FROM claim_message_job(120)")
    assert reclaimed is True


def test_fail_retries_then_dead_letters(conn):
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    # max_retries defaults to 3 -> failures 1,2 = 'failed', failure 3 = 'dead'.
    assert _scalar(conn, "SELECT fail_message_job('wamid.1', 'boom')") == "failed"
    assert _scalar(conn, "SELECT fail_message_job('wamid.1', 'boom')") == "failed"
    assert _scalar(conn, "SELECT fail_message_job('wamid.1', 'boom')") == "dead"
    rc = _scalar(conn, "SELECT retry_count FROM message_jobs WHERE message_id='wamid.1'")
    assert rc == 3


def test_claim_reclaims_stale_processing_job(conn):
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    conn.execute("SELECT claim_message_job()")
    # Simulate a worker that died mid-job 5 minutes ago.
    conn.execute("UPDATE message_jobs SET claimed_at = NOW() - INTERVAL '5 minutes'")
    status = _scalar(conn, "SELECT status FROM claim_message_job(120)")
    assert status == "processing"  # reclaimed


# --- booking alert dedup ---

def test_claim_booking_alert_is_atomic_only_one_winner(conn):
    _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")  # creates the row
    # First claim wins; a second concurrent claim for the same key is blocked
    # while the first is still 'sending' -> no duplicate owner alert.
    assert _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k1')") is True
    assert _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k1')") is False


def test_claim_booking_alert_sent_blocks_reclaim_failed_allows_retry(conn):
    _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")
    _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k1')")
    # Success -> 'sent' -> no re-claim for the same key.
    conn.execute("SELECT finish_booking_alert('whatsapp', 'a', 'k1', TRUE)")
    assert _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k1')") is False
    # A new booking key is always claimable.
    assert _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k2')") is True
    # Failure -> 're-claimable (not permanently suppressed).
    conn.execute("SELECT finish_booking_alert('whatsapp', 'a', 'k2', FALSE)")
    assert _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k2')") is True


def test_claim_booking_alert_reclaims_stale_sending(conn):
    _scalar(conn, "SELECT increment_daily_counter('whatsapp', 'a')")
    _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k1')")
    # Claiming worker died mid-send: 'sending' but claimed long ago.
    conn.execute("UPDATE conversations SET booking_alert_at = NOW() - INTERVAL '5 minutes'")
    assert _scalar(conn, "SELECT claim_booking_alert('whatsapp', 'a', 'k1', 120)") is True


def test_post_send_recovery_state(conn):
    """Reply sent + history appended, but succeed_message_job never ran (job left
    'processing'). A stale reclaim must expose both flags so the worker skips the
    resend and the duplicate history append."""
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    conn.execute("SELECT claim_message_job()")
    conn.execute("SELECT mark_reply_sent('wamid.1')")
    conn.execute("SELECT mark_history_appended('wamid.1')")
    # Simulate the crash before succeed: still 'processing', claimed long ago.
    conn.execute("UPDATE message_jobs SET claimed_at = NOW() - INTERVAL '5 minutes'")
    cur = conn.execute(
        "SELECT reply_sent, history_appended, status FROM claim_message_job(120)"
    )
    row = cur.fetchone()
    assert row == (True, True, "processing")


# --- conversation history append + cap ---

def test_append_conversation_turn_caps_history(conn):
    msgs = json.dumps([{"role": "user", "content": str(i)} for i in range(25)])
    conn.execute(
        "SELECT append_conversation_turn('whatsapp', 'a', %s::jsonb, 20)", (msgs,)
    )
    stored = _scalar(
        conn,
        "SELECT jsonb_array_length(messages) FROM conversations WHERE sender_id='a'",
    )
    assert stored == 20


def test_record_message_event_inserts(conn):
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    conn.execute("SELECT record_message_event('wamid.1', 'inbound', '{\"x\": 1}'::jsonb)")
    n = _scalar(conn, "SELECT count(*) FROM message_events WHERE message_id='wamid.1'")
    assert n == 1


def test_monitoring_views_resolve(conn):
    _scalar(conn, "SELECT enqueue_message('wamid.1', 'whatsapp', 'a', 'hi')")
    _scalar(conn, "SELECT fail_message_job('wamid.1', 'boom')")
    _scalar(conn, "SELECT fail_message_job('wamid.1', 'boom')")
    _scalar(conn, "SELECT fail_message_job('wamid.1', 'boom')")  # -> dead
    received = _scalar(conn, "SELECT jobs FROM queue_status WHERE status='dead'")
    assert received == 1
    dead = _scalar(conn, "SELECT count(*) FROM dead_letter_jobs")
    assert dead == 1
    # stuck_jobs view must at least be queryable.
    conn.execute("SELECT count(*) FROM stuck_jobs")
