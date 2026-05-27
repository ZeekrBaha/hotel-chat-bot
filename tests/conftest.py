import pytest


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "hotel-bot-verify-2026")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "79991234567")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", "system-prompt.txt")
