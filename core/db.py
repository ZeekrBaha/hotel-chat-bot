import logging
import os
from supabase import create_client, Client

MAX_HISTORY = 20
_logger = logging.getLogger(__name__)
_supabase_client: Client | None = None


def get_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
        )
    return _supabase_client


def check_health() -> None:
    """Real readiness probe: a lightweight round-trip to Supabase.

    Raises if the database is unreachable or credentials are wrong.
    """
    get_client().table("conversations").select("id").limit(1).execute()


def get_history(platform: str, sender_id: str) -> list[dict]:
    client = get_client()
    result = (
        client.table("conversations")
        .select("messages")
        .eq("platform", platform)
        .eq("sender_id", sender_id)
        .execute()
    )
    if result.data:
        return result.data[0]["messages"]
    return []


def increment_daily_counter(platform: str, sender_id: str) -> int:
    """Atomically increment via PostgreSQL RPC. Returns new count."""
    result = get_client().rpc("increment_daily_counter", {
        "p_platform": platform, "p_sender_id": sender_id,
    }).execute()
    return result.data if isinstance(result.data, int) else 1


def append_conversation_turn(platform: str, sender_id: str, messages: dict | list[dict]) -> None:
    """Atomically append message(s) to conversation history via RPC."""
    messages_list = messages if isinstance(messages, list) else [messages]
    get_client().rpc("append_conversation_turn", {
        "p_platform": platform,
        "p_sender_id": sender_id,
        "p_messages": messages_list,
        "p_max_history": MAX_HISTORY,
    }).execute()


def _booking_key(booking: dict) -> str:
    return (
        f"{booking.get('guest_name')}|{booking.get('check_in')}"
        f"|{booking.get('check_out')}|{booking.get('num_guests')}"
    )


def claim_booking_alert(platform: str, sender_id: str, booking: dict) -> bool:
    """Atomically claim the owner alert for this booking. Returns True to exactly
    one caller (parallel jobs for the same booking cannot both claim it). Call
    finish_booking_alert afterwards. Atomic via RPC."""
    result = get_client().rpc("claim_booking_alert", {
        "p_platform": platform,
        "p_sender_id": sender_id,
        "p_key": _booking_key(booking),
    }).execute()
    return result.data is True


def finish_booking_alert(platform: str, sender_id: str, booking: dict, success: bool) -> None:
    """Close out a claimed owner alert: 'sent' on success (terminal), 'failed' on
    failure (re-claimable, so a failed alert is retried, not suppressed)."""
    get_client().rpc("finish_booking_alert", {
        "p_platform": platform,
        "p_sender_id": sender_id,
        "p_key": _booking_key(booking),
        "p_success": success,
    }).execute()


# --- Durable message queue -------------------------------------------------

def enqueue_message(message_id: str, platform: str, sender_id: str, text: str) -> bool:
    """Enqueue an inbound message. Returns True if newly enqueued, False if it is a
    duplicate (a Meta retry of a message we already accepted). Atomic via RPC."""
    result = get_client().rpc("enqueue_message", {
        "p_message_id": message_id,
        "p_platform": platform,
        "p_sender_id": sender_id,
        "p_text": text,
    }).execute()
    if result.data is True:
        return True
    if result.data is not False:
        _logger.warning("enqueue_message unexpected result.data=%r message_id=%s", result.data, message_id)
    return False


def claim_message_job(stale_seconds: int = 120, retry_backoff_seconds: int = 30) -> dict | None:
    """Atomically claim the next workable job, or None if the queue is empty."""
    result = get_client().rpc("claim_message_job", {
        "p_stale_seconds": stale_seconds,
        "p_retry_backoff_seconds": retry_backoff_seconds,
    }).execute()
    rows = result.data
    if rows:
        return rows[0]
    return None


def save_job_result(message_id: str, result: dict) -> None:
    """Persist the generated bot result so retries re-send the same reply."""
    get_client().rpc("save_job_result", {
        "p_message_id": message_id,
        "p_result": result,
    }).execute()


def mark_reply_sent(message_id: str) -> None:
    """Record that the outbound reply was confirmed delivered, so a reclaimed job
    is not re-sent."""
    get_client().rpc("mark_reply_sent", {"p_message_id": message_id}).execute()


def mark_history_appended(message_id: str) -> None:
    """Record that the conversation turn was appended, so a reclaim does not append
    it again."""
    get_client().rpc("mark_history_appended", {"p_message_id": message_id}).execute()


def succeed_message_job(message_id: str) -> None:
    """Mark a job as successfully replied (terminal)."""
    get_client().rpc("succeed_message_job", {"p_message_id": message_id}).execute()


def fail_message_job(message_id: str, error: str) -> str | None:
    """Record a failure; the RPC retries or dead-letters. Returns new status."""
    result = get_client().rpc("fail_message_job", {
        "p_message_id": message_id,
        "p_error": error,
    }).execute()
    return result.data


def record_message_event(message_id: str, event_type: str, detail: dict | None = None) -> None:
    """Append an audit event. Never raises into the caller — auditing must not
    break message processing."""
    try:
        get_client().rpc("record_message_event", {
            "p_message_id": message_id,
            "p_event_type": event_type,
            "p_detail": detail,
        }).execute()
    except Exception:
        _logger.exception("record_message_event_failed message_id=%s type=%s", message_id, event_type)
