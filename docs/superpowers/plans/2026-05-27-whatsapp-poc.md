# Hotel Chat Bot — WhatsApp POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working WhatsApp bot that answers hotel FAQs in Russian/Kyrgyz, collects booking requests, and notifies the owner — all driven by Claude Haiku and Supabase for memory.

**Architecture:** Flask receives Meta WhatsApp webhooks, `platforms/whatsapp.py` parses and verifies them, `core/bot.py` calls Claude Haiku with conversation history from Supabase, `core/notify.py` alerts the owner on booking intent. All external calls are mocked in tests; no real API keys needed to run the test suite.

**Tech Stack:** Python 3.11, Flask 3.0, anthropic SDK, supabase-py, pytest, pytest-mock, requests

---

## File Map

| File | Responsibility |
|---|---|
| `requirements.txt` | Production dependencies |
| `requirements-dev.txt` | Test/dev dependencies |
| `.env.example` | Secret template — no real values |
| `sql/schema.sql` | Supabase table DDL |
| `tests/conftest.py` | Env-var fixtures for all tests |
| `core/__init__.py` | Empty package marker |
| `core/db.py` | Supabase get/save conversation history |
| `core/bot.py` | Claude call + booking intent detection |
| `core/notify.py` | Send WhatsApp alert to owner |
| `platforms/__init__.py` | Empty package marker |
| `platforms/whatsapp.py` | Parse inbound, verify HMAC, send reply |
| `app.py` | Flask routes — orchestrates all modules |
| `tests/__init__.py` | Empty package marker |
| `tests/test_db.py` | Tests for core/db.py |
| `tests/test_bot.py` | Tests for core/bot.py |
| `tests/test_notify.py` | Tests for core/notify.py |
| `tests/test_whatsapp.py` | Tests for platforms/whatsapp.py |
| `tests/test_app.py` | Tests for app.py (Flask routes) |
| `deploy/hotel-chat-bot.service` | systemd unit file |
| `deploy/nginx.conf` | nginx reverse proxy config |

---

## Task 1: Project Setup (no tests — config files only)

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `.env.example`
- Create: `sql/schema.sql`
- Create: `tests/conftest.py`
- Create: `core/__init__.py`, `platforms/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p core platforms tests sql deploy
touch core/__init__.py platforms/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

```
flask==3.0.3
gunicorn==21.2.0
anthropic==0.49.0
supabase==2.4.2
python-dotenv==1.0.1
requests==2.31.0
```

- [ ] **Step 3: Write `requirements-dev.txt`**

```
pytest==8.2.0
pytest-mock==3.14.0
```

- [ ] **Step 4: Write `.env.example`**

```bash
# Meta WhatsApp
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=hotel-bot-verify-2026
WHATSAPP_APP_SECRET=

# Owner notification
OWNER_PHONE_NUMBER=

# Anthropic
ANTHROPIC_API_KEY=

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_KEY=

# App
FLASK_ENV=production
PORT=8000
SYSTEM_PROMPT_PATH=system-prompt.txt
```

- [ ] **Step 5: Write `sql/schema.sql`**

```sql
CREATE TABLE conversations (
  id            BIGSERIAL PRIMARY KEY,
  platform      TEXT NOT NULL DEFAULT 'whatsapp',
  sender_id     TEXT NOT NULL,
  messages      JSONB NOT NULL DEFAULT '[]',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX conversations_platform_sender_idx
  ON conversations (platform, sender_id);
```

- [ ] **Step 6: Write `tests/conftest.py`**

```python
import pytest


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "hotel-bot-verify-2026")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "79991234567")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", "system-prompt.txt")
```

- [ ] **Step 7: Install dev dependencies**

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

- [ ] **Step 8: Verify pytest is working**

```bash
pytest --collect-only
```

Expected: `no tests ran` (no test files yet — that's correct)

- [ ] **Step 9: Commit**

```bash
git add requirements.txt requirements-dev.txt .env.example sql/schema.sql \
        tests/conftest.py core/__init__.py platforms/__init__.py tests/__init__.py
git commit -m "feat: project setup — dependencies, schema, test config"
```

---

## Task 2: core/db.py — Supabase data layer

**Files:**
- Create: `core/db.py`
- Create: `tests/test_db.py`

### RED — write all failing tests first

- [ ] **Step 1: Write `tests/test_db.py`**

```python
from unittest.mock import MagicMock, patch
from core.db import get_history, save_history


def _make_mock_client(data=None):
    """Return a mock supabase client pre-configured for the chained query API."""
    mock = MagicMock()
    mock.table.return_value \
        .select.return_value \
        .eq.return_value \
        .eq.return_value \
        .execute.return_value \
        .data = data or []
    return mock


def test_get_history_returns_empty_list_when_no_record():
    mock_client = _make_mock_client(data=[])
    with patch("core.db.get_client", return_value=mock_client):
        result = get_history("whatsapp", "79991234567")
    assert result == []


def test_get_history_returns_messages_when_record_exists():
    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Добрый день!"},
    ]
    mock_client = _make_mock_client(data=[{"messages": messages}])
    with patch("core.db.get_client", return_value=mock_client):
        result = get_history("whatsapp", "79991234567")
    assert result == messages


def test_save_history_calls_upsert_with_correct_payload():
    mock_client = MagicMock()
    messages = [{"role": "user", "content": "Привет"}]
    with patch("core.db.get_client", return_value=mock_client):
        save_history("whatsapp", "79991234567", messages)
    mock_client.table.return_value.upsert.assert_called_once_with(
        {"platform": "whatsapp", "sender_id": "79991234567", "messages": messages},
        on_conflict="platform,sender_id",
    )


def test_save_history_trims_to_last_20_messages():
    mock_client = MagicMock()
    messages = [{"role": "user", "content": str(i)} for i in range(25)]
    with patch("core.db.get_client", return_value=mock_client):
        save_history("whatsapp", "79991234567", messages)
    saved = mock_client.table.return_value.upsert.call_args[0][0]["messages"]
    assert len(saved) == 20
    assert saved[0]["content"] == "5"   # oldest 5 dropped
    assert saved[-1]["content"] == "24"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db.py -v
```

Expected: `ImportError: No module named 'core.db'`

### GREEN — implement

- [ ] **Step 3: Write `core/db.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add core/db.py tests/test_db.py
git commit -m "feat: core/db — Supabase get/save conversation history"
```

---

## Task 3: core/bot.py — OpenAI call + booking intent

**Files:**
- Create: `core/bot.py`
- Create: `tests/test_bot.py`

### RED — write all failing tests first

- [ ] **Step 1: Write `tests/test_bot.py`**

```python
from unittest.mock import MagicMock, patch
from core.bot import handle_message, is_booking_intent

FAKE_PROMPT = "Ты — администратор отеля."
FAKE_REPLY = "Добрый день! Чем могу помочь?"


def _mock_openai_response(text: str):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


# --- is_booking_intent ---

def test_is_booking_intent_detects_russian_keyword():
    assert is_booking_intent("Хочу забронировать номер") is True


def test_is_booking_intent_detects_kyrgyz_keyword():
    assert is_booking_intent("бронь бар беле") is True


def test_is_booking_intent_ignores_unrelated_message():
    assert is_booking_intent("Сколько стоит номер?") is False


def test_is_booking_intent_is_case_insensitive():
    assert is_booking_intent("ЗАБРОНИРОВАТЬ") is True


# --- handle_message ---

def test_handle_message_calls_openai_with_system_prompt_and_history():
    mock_history = [{"role": "user", "content": "Привет"}]
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_openai_response(FAKE_REPLY)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=mock_history), \
         patch("core.bot.db.save_history"), \
         patch("core.bot.OpenAI", return_value=mock_openai):

        result = handle_message("whatsapp", "79991234567", "Добрый день")

    mock_openai.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["max_tokens"] == 400
    assert call_kwargs["messages"][0] == {"role": "system", "content": FAKE_PROMPT}
    assert result == FAKE_REPLY


def test_handle_message_appends_user_and_assistant_to_history():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_openai_response(FAKE_REPLY)
    saved = {}

    def capture_save(platform, sender_id, messages):
        saved["messages"] = messages

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.save_history", side_effect=capture_save), \
         patch("core.bot.OpenAI", return_value=mock_openai):

        handle_message("whatsapp", "79991234567", "Здравствуйте")

    assert saved["messages"][-2] == {"role": "user", "content": "Здравствуйте"}
    assert saved["messages"][-1] == {"role": "assistant", "content": FAKE_REPLY}


def test_handle_message_passes_last_10_messages_to_openai():
    long_history = [{"role": "user", "content": str(i)} for i in range(15)]
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_openai_response(FAKE_REPLY)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=long_history), \
         patch("core.bot.db.save_history"), \
         patch("core.bot.OpenAI", return_value=mock_openai):

        handle_message("whatsapp", "79991234567", "Новое сообщение")

    sent_messages = mock_openai.chat.completions.create.call_args.kwargs["messages"]
    # system message + last 10 from history (15 existing + 1 new = 16, take last 10)
    assert sent_messages[0]["role"] == "system"
    assert len(sent_messages) == 11
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_bot.py -v
```

Expected: `ImportError: No module named 'core.bot'`

### GREEN — implement

- [ ] **Step 3: Write `core/bot.py`**

```python
import os
from openai import OpenAI
from core import db

BOOKING_KEYWORDS = [
    "забронировать", "бронь", "свободен", "хочу номер",
    "book", "reserve", "бронирование",
]
CONTEXT_WINDOW = 10


def get_system_prompt() -> str:
    path = os.environ.get("SYSTEM_PROMPT_PATH", "system-prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def is_booking_intent(message_text: str) -> bool:
    text_lower = message_text.lower()
    return any(kw in text_lower for kw in BOOKING_KEYWORDS)


def handle_message(platform: str, sender_id: str, message_text: str) -> str:
    history = db.get_history(platform, sender_id)
    history.append({"role": "user", "content": message_text})

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=400,
        messages=[
            {"role": "system", "content": get_system_prompt()},
            *history[-CONTEXT_WINDOW:],
        ],
    )
    reply = response.choices[0].message.content

    history.append({"role": "assistant", "content": reply})
    db.save_history(platform, sender_id, history)

    return reply
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_bot.py -v
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot.py tests/test_bot.py
git commit -m "feat: core/bot — OpenAI gpt-4o-mini call, history, booking intent detection"
```

---

## Task 4: core/notify.py — Owner WhatsApp alert

**Files:**
- Create: `core/notify.py`
- Create: `tests/test_notify.py`

### RED — write all failing tests first

- [ ] **Step 1: Write `tests/test_notify.py`**

```python
from unittest.mock import patch, call
from core.notify import send_owner_alert


def test_send_owner_alert_posts_to_correct_url():
    with patch("core.notify.requests.post") as mock_post:
        send_owner_alert(
            sender_id="79991234567",
            platform="whatsapp",
            message_text="Хочу забронировать",
            bot_reply="Спасибо! Уточните даты.",
        )
    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    assert "123456789" in url   # WHATSAPP_PHONE_NUMBER_ID from conftest


def test_send_owner_alert_includes_bearer_token():
    with patch("core.notify.requests.post") as mock_post:
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"


def test_send_owner_alert_message_body_contains_key_info():
    with patch("core.notify.requests.post") as mock_post:
        send_owner_alert(
            sender_id="79991234567",
            platform="whatsapp",
            message_text="Хочу забронировать",
            bot_reply="Уточните имя и даты.",
        )
    body = mock_post.call_args.kwargs["json"]["text"]["body"]
    assert "79991234567" in body
    assert "whatsapp" in body
    assert "Хочу забронировать" in body
    assert "Уточните имя и даты." in body


def test_send_owner_alert_sends_to_owner_number():
    with patch("core.notify.requests.post") as mock_post:
        send_owner_alert("79991234567", "whatsapp", "msg", "reply")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["to"] == "79991234567"   # OWNER_PHONE_NUMBER from conftest
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_notify.py -v
```

Expected: `ImportError: No module named 'core.notify'`

### GREEN — implement

- [ ] **Step 3: Write `core/notify.py`**

```python
import os
import requests


def send_owner_alert(
    sender_id: str, platform: str, message_text: str, bot_reply: str
) -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    owner_number = os.environ["OWNER_PHONE_NUMBER"]

    body = (
        f"Новая заявка на бронирование\n"
        f"Платформа: {platform}\n"
        f"Гость: {sender_id}\n"
        f"Сообщение: {message_text}\n"
        f"Ответ бота: {bot_reply}"
    )

    requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": owner_number,
            "type": "text",
            "text": {"body": body},
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_notify.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add core/notify.py tests/test_notify.py
git commit -m "feat: core/notify — owner WhatsApp booking alert"
```

---

## Task 5: platforms/whatsapp.py — Parse, verify, send

**Files:**
- Create: `platforms/whatsapp.py`
- Create: `tests/test_whatsapp.py`

### RED — write all failing tests first

- [ ] **Step 1: Write `tests/test_whatsapp.py`**

```python
import hashlib
import hmac as hmac_module
from unittest.mock import patch
from platforms.whatsapp import parse_inbound, verify_signature, send_reply

# --- parse_inbound ---

VALID_PAYLOAD = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": "79991234567",
                    "type": "text",
                    "text": {"body": "Здравствуйте"},
                }]
            }
        }]
    }]
}


def test_parse_inbound_returns_phone_and_text():
    phone, text = parse_inbound(VALID_PAYLOAD)
    assert phone == "79991234567"
    assert text == "Здравствуйте"


def test_parse_inbound_returns_none_for_non_text_message():
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{"from": "123", "type": "image"}]}}]}]
    }
    assert parse_inbound(payload) is None


def test_parse_inbound_returns_none_for_malformed_payload():
    assert parse_inbound({}) is None
    assert parse_inbound({"entry": []}) is None


# --- verify_signature ---

def _make_sig(secret: str, payload: bytes) -> str:
    h = hmac_module.new(secret.encode(), payload, hashlib.sha256)
    return f"sha256={h.hexdigest()}"


def test_verify_signature_accepts_valid_signature():
    payload = b'{"test": "data"}'
    sig = _make_sig("test-app-secret", payload)
    assert verify_signature(payload, sig, "test-app-secret") is True


def test_verify_signature_rejects_wrong_secret():
    payload = b'{"test": "data"}'
    sig = _make_sig("wrong-secret", payload)
    assert verify_signature(payload, sig, "test-app-secret") is False


def test_verify_signature_rejects_missing_prefix():
    assert verify_signature(b"data", "invalidsignature", "secret") is False


def test_verify_signature_rejects_tampered_payload():
    sig = _make_sig("test-app-secret", b"original")
    assert verify_signature(b"tampered", sig, "test-app-secret") is False


# --- send_reply ---

def test_send_reply_posts_to_correct_endpoint():
    with patch("platforms.whatsapp.requests.post") as mock_post:
        send_reply("79991234567", "Добрый день!")
    url = mock_post.call_args.args[0]
    assert "123456789" in url   # WHATSAPP_PHONE_NUMBER_ID from conftest


def test_send_reply_sends_correct_payload():
    with patch("platforms.whatsapp.requests.post") as mock_post:
        send_reply("79991234567", "Добрый день!")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["to"] == "79991234567"
    assert payload["text"]["body"] == "Добрый день!"
    assert payload["messaging_product"] == "whatsapp"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_whatsapp.py -v
```

Expected: `ImportError: No module named 'platforms.whatsapp'`

### GREEN — implement

- [ ] **Step 3: Write `platforms/whatsapp.py`**

```python
import hashlib
import hmac
import os
import requests


def parse_inbound(payload: dict) -> tuple[str, str] | None:
    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        if message["type"] != "text":
            return None
        return message["from"], message["text"]["body"]
    except (KeyError, IndexError):
        return None


def verify_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    received = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


def send_reply(phone_number: str, text: str) -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": text},
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_whatsapp.py -v
```

Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add platforms/whatsapp.py tests/test_whatsapp.py
git commit -m "feat: platforms/whatsapp — parse, HMAC verify, send reply"
```

---

## Task 6: app.py — Flask routes

**Files:**
- Create: `app.py`
- Create: `tests/test_app.py`

### RED — write all failing tests first

- [ ] **Step 1: Write `tests/test_app.py`**

```python
import hashlib
import hmac as hmac_module
import json
from unittest.mock import patch
import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _sign(payload: bytes, secret: str) -> str:
    h = hmac_module.new(secret.encode(), payload, hashlib.sha256)
    return f"sha256={h.hexdigest()}"


# --- GET /health ---

def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


# --- GET /whatsapp/webhook (Meta verification) ---

def test_whatsapp_verify_returns_challenge_on_valid_token(client):
    response = client.get(
        "/whatsapp/webhook",
        query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "hotel-bot-verify-2026",
            "hub.challenge": "abc123",
        },
    )
    assert response.status_code == 200
    assert response.data == b"abc123"


def test_whatsapp_verify_returns_403_on_wrong_token(client):
    response = client.get(
        "/whatsapp/webhook",
        query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "abc123",
        },
    )
    assert response.status_code == 403


# --- POST /whatsapp/webhook ---

def _inbound_payload(phone="79991234567", text="Здравствуйте"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": phone,
                        "type": "text",
                        "text": {"body": text},
                    }]
                }
            }]
        }]
    }


def test_whatsapp_inbound_rejects_bad_signature(client):
    payload = json.dumps(_inbound_payload()).encode()
    response = client.post(
        "/whatsapp/webhook",
        data=payload,
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=badsignature"},
    )
    assert response.status_code == 401


def test_whatsapp_inbound_returns_200_and_calls_bot(client):
    payload = json.dumps(_inbound_payload()).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message", return_value="Добрый день!") as mock_bot, \
         patch("app.whatsapp.send_reply") as mock_send, \
         patch("app.bot.is_booking_intent", return_value=False):

        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_bot.assert_called_once_with("whatsapp", "79991234567", "Здравствуйте")
    mock_send.assert_called_once_with("79991234567", "Добрый день!")


def test_whatsapp_inbound_sends_owner_alert_on_booking_intent(client):
    payload = json.dumps(_inbound_payload(text="Хочу забронировать")).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message", return_value="Уточните даты."), \
         patch("app.whatsapp.send_reply"), \
         patch("app.bot.is_booking_intent", return_value=True), \
         patch("app.notify.send_owner_alert") as mock_notify:

        client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    mock_notify.assert_called_once_with(
        "79991234567", "whatsapp", "Хочу забронировать", "Уточните даты."
    )


def test_whatsapp_inbound_returns_200_for_non_text_message(client):
    payload = json.dumps({
        "entry": [{"changes": [{"value": {"messages": [{"from": "123", "type": "image"}]}}]}]
    }).encode()
    sig = _sign(payload, "test-app-secret")

    with patch("app.bot.handle_message") as mock_bot:
        response = client.post(
            "/whatsapp/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )

    assert response.status_code == 200
    mock_bot.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_app.py -v
```

Expected: `ImportError: No module named 'app'`

### GREEN — implement

- [ ] **Step 3: Write `app.py`**

```python
import os
from flask import Flask, request
from core import bot, notify
from platforms import whatsapp

app = Flask(__name__)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/whatsapp/webhook")
def whatsapp_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.environ["WHATSAPP_VERIFY_TOKEN"]:
        return challenge, 200
    return "Forbidden", 403


@app.post("/whatsapp/webhook")
def whatsapp_inbound():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not whatsapp.verify_signature(request.data, sig, os.environ["WHATSAPP_APP_SECRET"]):
        return "Unauthorized", 401

    parsed = whatsapp.parse_inbound(request.json)
    if parsed is None:
        return "", 200

    phone, text = parsed
    reply = bot.handle_message("whatsapp", phone, text)

    if bot.is_booking_intent(text):
        notify.send_owner_alert(phone, "whatsapp", text, reply)

    whatsapp.send_reply(phone, reply)
    return "", 200
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_app.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass (no failures, no warnings)

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: app.py — Flask routes for WhatsApp webhook"
```

---

## Task 7: Deploy configs (no tests — config files only)

**Files:**
- Create: `deploy/hotel-chat-bot.service`
- Create: `deploy/nginx.conf`

- [ ] **Step 1: Write `deploy/hotel-chat-bot.service`**

```ini
[Unit]
Description=Hotel Chat Bot
After=network.target

[Service]
User=hotelbot
WorkingDirectory=/home/hotelbot/hotel-chat-bot
EnvironmentFile=/home/hotelbot/hotel-chat-bot/.env
ExecStart=/home/hotelbot/hotel-chat-bot/venv/bin/gunicorn \
    --workers 2 \
    --bind 127.0.0.1:8000 \
    --access-logfile - \
    --error-logfile - \
    app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `deploy/nginx.conf`**

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 10s;
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add deploy/hotel-chat-bot.service deploy/nginx.conf
git commit -m "feat: deploy — systemd service and nginx config"
```

---

## Self-Review

**Spec coverage check:**
- FR-1 (replies in Russian ≤5s): Flask + gunicorn handles this; Claude call is the bottleneck, max_tokens=400 keeps it fast ✓
- FR-2 (FAQ answers): system-prompt.txt + Claude handles this ✓
- FR-3 (booking intent via keywords): `is_booking_intent()` in core/bot.py ✓
- FR-4 (collects name/dates/guests): Claude handles via system-prompt rules ✓
- FR-5 (notify owner via WhatsApp): core/notify.py ✓
- FR-6 (Kyrgyz response): system-prompt.txt language rules ✓
- FR-7 (English response): removed from spec (Russian/Kyrgyz only) ✓
- FR-8 (never shares payment details): system-prompt.txt rule 5 ✓
- FR-9 (history persists in Supabase): core/db.py ✓
- FR-10 (system prompt editable without redeploy): `system-prompt.txt` file + restart ✓
- NFR-5 (HTTPS): nginx config ✓
- NFR-6 (webhook signature): `verify_signature()` in platforms/whatsapp.py + app.py ✓

**Placeholder scan:** No TBDs, no TODOs, no "similar to" references found.

**Type consistency:** `handle_message(platform, sender_id, message_text)` used consistently across bot.py, app.py, and tests. `send_owner_alert(sender_id, platform, message_text, bot_reply)` consistent across notify.py and test_app.py.
