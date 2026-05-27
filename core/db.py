import datetime
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


def is_duplicate_message(message_id: str) -> bool:
    """Returns True if already processed. Atomic INSERT ON CONFLICT via RPC."""
    result = get_client().rpc(
        "mark_message_processed", {"p_message_id": message_id}
    ).execute()
    if result.data is False:
        return True
    if result.data is not True:
        _logger.warning("is_duplicate_message unexpected result.data=%r message_id=%s", result.data, message_id)
    return False


def check_and_set_booking_alert(platform: str, sender_id: str, booking: dict) -> bool:
    """Returns True only if booking key changed (alert should fire). Atomic via RPC."""
    key = (
        f"{booking.get('guest_name')}|{booking.get('check_in')}"
        f"|{booking.get('check_out')}|{booking.get('num_guests')}"
    )
    result = get_client().rpc("set_booking_alert_if_new", {
        "p_platform": platform,
        "p_sender_id": sender_id,
        "p_key": key,
    }).execute()
    return result.data is True
