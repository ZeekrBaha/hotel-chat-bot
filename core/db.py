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


def save_history(platform: str, sender_id: str, messages: list[dict]) -> None:
    trimmed = messages[-MAX_HISTORY:]
    get_client().table("conversations").upsert(
        {
            "platform": platform,
            "sender_id": sender_id,
            "messages": trimmed,
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        },
        on_conflict="platform,sender_id",
    ).execute()


def is_duplicate_message(message_id: str) -> bool:
    """Returns True if already processed. Atomic INSERT ON CONFLICT via RPC."""
    result = get_client().rpc(
        "mark_message_processed", {"p_message_id": message_id}
    ).execute()
    return not result.data


def check_and_set_booking_alert(platform: str, sender_id: str, booking: dict) -> bool:
    """Returns True (and persists key) only if this booking tuple is new for this sender."""
    key = (
        f"{booking.get('guest_name')}|{booking.get('check_in')}"
        f"|{booking.get('check_out')}|{booking.get('num_guests')}"
    )
    client = get_client()
    result = (
        client.table("conversations")
        .select("last_alerted_booking_key")
        .eq("platform", platform)
        .eq("sender_id", sender_id)
        .execute()
    )
    if result.data and result.data[0].get("last_alerted_booking_key") == key:
        return False
    client.table("conversations").update(
        {"last_alerted_booking_key": key}
    ).eq("platform", platform).eq("sender_id", sender_id).execute()
    return True
