import os
import pytest

# Set required env vars before any app module is imported (collection time).
_TEST_ENV = {
    "WHATSAPP_ACCESS_TOKEN": "test-token",
    "WHATSAPP_PHONE_NUMBER_ID": "123456789",
    "WHATSAPP_VERIFY_TOKEN": "hotel-bot-verify-2026",
    "WHATSAPP_APP_SECRET": "test-app-secret",
    "OWNER_PHONE_NUMBER": "79991234567",
    "OPENAI_API_KEY": "test-openai-key",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "SYSTEM_PROMPT_PATH": "system-prompt.txt",
}

def pytest_configure(config):
    for key, value in _TEST_ENV.items():
        os.environ.setdefault(key, value)


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
