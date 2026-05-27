import os
from supabase import create_client, Client

MAX_HISTORY = 20


def get_client() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


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


def save_history(platform: str, sender_id: str, messages: list[dict]) -> None:
    trimmed = messages[-MAX_HISTORY:]
    client = get_client()
    client.table("conversations").upsert(
        {"platform": platform, "sender_id": sender_id, "messages": trimmed},
        on_conflict="platform,sender_id",
    ).execute()
