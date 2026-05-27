import json
from unittest.mock import MagicMock, patch
from core.bot import handle_message, is_booking_intent

FAKE_PROMPT = "Ты — администратор отеля."
FAKE_REPLY = "Добрый день! Чем могу помочь?"


def _mock_openai_response(text: str):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


def _mock_structured_response(reply: str, is_booking: bool, guest_name: str | None = None):
    payload = json.dumps({"reply": reply, "is_booking_intent": is_booking, "guest_name": guest_name})
    return _mock_openai_response(payload)


# --- is_booking_intent (keyword fallback) ---

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
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=mock_history), \
         patch("core.bot.db.save_history"), \
         patch("core.bot._get_openai_client", return_value=mock_openai):

        result = handle_message("whatsapp", "79991234567", "Добрый день")

    mock_openai.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["max_tokens"] == 400
    assert call_kwargs["messages"][0]["role"] == "system"
    assert FAKE_PROMPT in call_kwargs["messages"][0]["content"]
    assert result["reply"] == FAKE_REPLY


def test_handle_message_returns_structured_dict():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(
        FAKE_REPLY, True, "Иван"
    )

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.save_history"), \
         patch("core.bot._get_openai_client", return_value=mock_openai):

        result = handle_message("whatsapp", "79991234567", "Хочу забронировать")

    assert result["reply"] == FAKE_REPLY
    assert result["is_booking_intent"] is True
    assert result["guest_name"] == "Иван"


def test_handle_message_uses_structured_output_response_format():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.save_history"), \
         patch("core.bot._get_openai_client", return_value=mock_openai):

        handle_message("whatsapp", "79991234567", "Привет")

    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert "response_format" in call_kwargs
    assert call_kwargs["response_format"]["type"] == "json_schema"


def test_handle_message_keyword_fallback_fires_when_llm_misses_intent():
    mock_openai = MagicMock()
    # LLM says no booking intent, but message contains a keyword
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.save_history"), \
         patch("core.bot._get_openai_client", return_value=mock_openai):

        result = handle_message("whatsapp", "79991234567", "Хочу забронировать номер")

    # Keyword "забронировать" is a safety net — should fire even when LLM returns false
    assert result["is_booking_intent"] is True


def test_handle_message_appends_user_and_assistant_to_history():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)
    saved = {}

    def capture_save(platform, sender_id, messages):
        saved["messages"] = messages

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.save_history", side_effect=capture_save), \
         patch("core.bot._get_openai_client", return_value=mock_openai):

        handle_message("whatsapp", "79991234567", "Здравствуйте")

    assert saved["messages"][-2] == {"role": "user", "content": "Здравствуйте"}
    assert saved["messages"][-1] == {"role": "assistant", "content": FAKE_REPLY}


def test_openai_client_configured_with_timeout_and_retries():
    """_get_openai_client() should build client with timeout=10.0 and max_retries=2."""
    import core.bot as bot_module
    bot_module._openai_client = None
    with patch("core.bot.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        bot_module._get_openai_client()
    mock_cls.assert_called_once_with(
        api_key="test-openai-key",
        timeout=10.0,
        max_retries=2,
    )
    bot_module._openai_client = None


def test_handle_message_injects_todays_date_in_system_prompt():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.save_history"), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):
        handle_message("whatsapp", "79991234567", "Добрый день")

    system_content = mock_openai.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Сегодня: 27.05.2026" in system_content
    assert FAKE_PROMPT in system_content


def test_get_system_prompt_is_cached():
    from unittest.mock import mock_open
    import core.bot as bot_module
    bot_module.get_system_prompt.cache_clear()
    m = mock_open(read_data="prompt content")
    with patch("builtins.open", m):
        bot_module.get_system_prompt()
        bot_module.get_system_prompt()
    assert m.call_count == 1  # file opened only once despite two calls
    bot_module.get_system_prompt.cache_clear()


def test_handle_message_passes_last_10_messages_to_openai():
    long_history = [{"role": "user", "content": str(i)} for i in range(15)]
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=long_history), \
         patch("core.bot.db.save_history"), \
         patch("core.bot._get_openai_client", return_value=mock_openai):

        handle_message("whatsapp", "79991234567", "Новое сообщение")

    sent_messages = mock_openai.chat.completions.create.call_args.kwargs["messages"]
    # system + last 10 from (15 existing + 1 new appended = 16, [-10:] = indices 6..15)
    assert sent_messages[0]["role"] == "system"
    assert len(sent_messages) == 11
    assert sent_messages[-1]["content"] == "Новое сообщение"
    assert sent_messages[1]["content"] == "6"
