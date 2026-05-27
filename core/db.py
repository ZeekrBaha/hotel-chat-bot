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
    """Increment messages_today, resetting if the date has changed. Returns new count."""
    today = datetime.date.today().isoformat()
    client = get_client()
    result = (
        client.table("conversations")
        .select("messages_today, counter_reset_at")
        .eq("platform", platform)
        .eq("sender_id", sender_id)
        .execute()
    )
    if not result.data:
        return 1  # row will be created by save_history; first message counts as 1
    row = result.data[0]
    reset_date = (row.get("counter_reset_at") or "")[:10]
    if reset_date < today:
        new_count = 1
        client.table("conversations").update({
            "messages_today": 1,
            "counter_reset_at": f"{today}T00:00:00Z",
        }).eq("platform", platform).eq("sender_id", sender_id).execute()
    else:
        new_count = (row.get("messages_today") or 0) + 1
        client.table("conversations").update({
            "messages_today": new_count,
        }).eq("platform", platform).eq("sender_id", sender_id).execute()
    return new_count


def save_history(platform: str, sender_id: str, messages: list[dict]) -> None:
    trimmed = messages[-MAX_HISTORY:]
    client = get_client()
    client.table("conversations").upsert(
        {"platform": platform, "sender_id": sender_id, "messages": trimmed},
        on_conflict="platform,sender_id",
    ).execute()
