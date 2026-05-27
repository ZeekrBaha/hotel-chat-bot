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


def _mock_structured_response(
    reply: str,
    is_booking: bool,
    guest_name: str | None = None,
    check_in: str | None = None,
    check_out: str | None = None,
    num_guests: int | None = None,
):
    payload = json.dumps({
        "reply": reply,
        "is_booking_intent": is_booking,
        "guest_name": guest_name,
        "check_in": check_in,
        "check_out": check_out,
        "num_guests": num_guests,
    })
    return _mock_openai_response(payload)


def _handle_message_patches(mock_openai, history=None):
    """Common patch context for handle_message tests."""
    return [
        patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT),
        patch("core.bot.db.get_history", return_value=history or []),
        patch("core.bot.db.append_conversation_turn"),
        patch("core.bot.db.increment_daily_counter", return_value=1),
        patch("core.bot._get_openai_client", return_value=mock_openai),
        patch("core.bot._today", return_value="27.05.2026"),
    ]


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
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        result = handle_message("whatsapp", "79991234567", "Добрый день")

    mock_openai.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["max_completion_tokens"] == 400
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
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        result = handle_message("whatsapp", "79991234567", "Хочу забронировать")

    assert result["reply"] == FAKE_REPLY
    assert result["is_booking_intent"] is True
    assert result["guest_name"] == "Иван"
    assert result["escalated"] is False


def test_handle_message_returns_all_booking_slots():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(
        FAKE_REPLY, True,
        guest_name="Айгуль",
        check_in="05.06.2026",
        check_out="07.06.2026",
        num_guests=2,
    )

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        result = handle_message("whatsapp", "79991234567", "Хочу забронировать на 5-7 июня")

    assert result["guest_name"] == "Айгуль"
    assert result["check_in"] == "05.06.2026"
    assert result["check_out"] == "07.06.2026"
    assert result["num_guests"] == 2


def test_handle_message_uses_structured_output_response_format():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        handle_message("whatsapp", "79991234567", "Привет")

    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert "response_format" in call_kwargs
    assert call_kwargs["response_format"]["type"] == "json_schema"


def test_handle_message_trusts_llm_over_keywords():
    """When LLM says False, result is False even if a booking keyword is present."""
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        result = handle_message("whatsapp", "79991234567", "Хочу забронировать номер")

    assert result["is_booking_intent"] is False


def test_handle_message_injects_todays_date_in_system_prompt():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):
        handle_message("whatsapp", "79991234567", "Добрый день")

    system_content = mock_openai.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Сегодня: 27.05.2026" in system_content
    assert FAKE_PROMPT in system_content


def test_handle_message_appends_user_and_assistant_to_history():
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=[]), \
         patch("core.bot.db.append_conversation_turn") as mock_append, \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        handle_message("whatsapp", "79991234567", "Здравствуйте")

    assert mock_append.call_count == 1
    call_args = mock_append.call_args[0]
    assert call_args[0] == "whatsapp"
    assert call_args[1] == "79991234567"
    assert call_args[2] == [
        {"role": "user", "content": "Здравствуйте"},
        {"role": "assistant", "content": FAKE_REPLY},
    ]


def test_handle_message_returns_escalation_when_daily_limit_exceeded():
    with patch("core.bot.db.increment_daily_counter", return_value=51):
        result = handle_message("whatsapp", "79991234567", "ещё один вопрос")

    assert result["escalated"] is True
    assert result["reply"] != ""
    assert result["is_booking_intent"] is False


def test_handle_message_skips_openai_when_daily_limit_exceeded():
    mock_openai = MagicMock()

    with patch("core.bot.db.increment_daily_counter", return_value=51), \
         patch("core.bot._get_openai_client", return_value=mock_openai):
        handle_message("whatsapp", "79991234567", "ещё один вопрос")

    mock_openai.chat.completions.create.assert_not_called()


def test_handle_message_escalated_false_after_transition():
    """Messages well past the limit (count > 51) still return escalation reply but escalated=False."""
    with patch("core.bot.db.increment_daily_counter", return_value=55):
        result = handle_message("whatsapp", "79991234567", "ещё один вопрос")

    assert result["escalated"] is False
    assert result["reply"] == "Ваш запрос передан администратору. Пожалуйста, ожидайте ответа."


def test_openai_client_configured_with_timeout_and_retries():
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


def test_get_system_prompt_is_cached():
    from unittest.mock import mock_open
    import core.bot as bot_module
    bot_module.get_system_prompt.cache_clear()
    m = mock_open(read_data="prompt content")
    with patch("builtins.open", m):
        bot_module.get_system_prompt()
        bot_module.get_system_prompt()
    assert m.call_count == 1
    bot_module.get_system_prompt.cache_clear()


def test_handle_message_passes_last_10_messages_to_openai():
    long_history = [{"role": "user", "content": str(i)} for i in range(15)]
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = _mock_structured_response(FAKE_REPLY, False)

    with patch("core.bot.get_system_prompt", return_value=FAKE_PROMPT), \
         patch("core.bot.db.get_history", return_value=long_history), \
         patch("core.bot.db.append_conversation_turn"), \
         patch("core.bot.db.increment_daily_counter", return_value=1), \
         patch("core.bot._get_openai_client", return_value=mock_openai), \
         patch("core.bot._today", return_value="27.05.2026"):

        handle_message("whatsapp", "79991234567", "Новое сообщение")

    sent_messages = mock_openai.chat.completions.create.call_args.kwargs["messages"]
    assert sent_messages[0]["role"] == "system"
    assert len(sent_messages) == 11
    assert sent_messages[-1]["content"] == "Новое сообщение"
    assert sent_messages[1]["content"] == "6"
