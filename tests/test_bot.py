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
