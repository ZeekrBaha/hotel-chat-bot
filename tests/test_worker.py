from unittest.mock import patch
import core.worker as worker


def _result(reply="Добрый день!", escalated=False, persist_history=True, is_booking=False,
            guest_name=None, check_in=None, check_out=None, num_guests=None):
    return {
        "reply": reply,
        "is_booking_intent": is_booking,
        "guest_name": guest_name,
        "check_in": check_in,
        "check_out": check_out,
        "num_guests": num_guests,
        "escalated": escalated,
        "persist_history": persist_history,
    }


def _complete_booking_result(reply="Бронь подтверждена."):
    return _result(reply=reply, is_booking=True, guest_name="Айгуль",
                   check_in="2026-06-05", check_out="2026-06-07", num_guests=2)


def _job(message_id="wamid.1", text="привет", result=None):
    return {
        "message_id": message_id,
        "platform": "whatsapp",
        "sender_id": "79991234567",
        "text": text,
        "result": result,
        "retry_count": 0,
        "max_retries": 3,
    }


# --- process_job: happy path ---

def test_process_job_generates_sends_and_succeeds():
    with patch("core.worker.bot.handle_message", return_value=_result()) as mock_bot, \
         patch("core.worker.db.save_job_result") as mock_save, \
         patch("core.worker.whatsapp.send_reply", return_value=True) as mock_send, \
         patch("core.worker.db.mark_reply_sent") as mock_mark, \
         patch("core.worker.db.append_conversation_turn") as mock_append, \
         patch("core.worker.db.mark_history_appended") as mock_mark_hist, \
         patch("core.worker.db.succeed_message_job") as mock_succeed, \
         patch("core.worker.db.fail_message_job") as mock_fail, \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job(text="привет"))

    mock_bot.assert_called_once_with("whatsapp", "79991234567", "привет")
    mock_save.assert_called_once()
    mock_send.assert_called_once_with("79991234567", "Добрый день!")
    mock_mark.assert_called_once_with("wamid.1")
    mock_append.assert_called_once()
    mock_mark_hist.assert_called_once_with("wamid.1")
    mock_succeed.assert_called_once_with("wamid.1")
    mock_fail.assert_not_called()


# --- process_job: send failure -> retry, no success, no history ---

def test_process_job_fails_when_send_fails():
    with patch("core.worker.bot.handle_message", return_value=_result()), \
         patch("core.worker.db.save_job_result"), \
         patch("core.worker.whatsapp.send_reply", return_value=False), \
         patch("core.worker.db.append_conversation_turn") as mock_append, \
         patch("core.worker.db.succeed_message_job") as mock_succeed, \
         patch("core.worker.db.fail_message_job", return_value="failed") as mock_fail, \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job())

    mock_append.assert_not_called()
    mock_succeed.assert_not_called()
    mock_fail.assert_called_once()


# --- process_job: retry reuses the persisted result (no OpenAI re-call) ---

def test_process_job_retry_reuses_stored_result():
    stored = _result(reply="Сохранённый ответ")
    with patch("core.worker.bot.handle_message") as mock_bot, \
         patch("core.worker.db.save_job_result") as mock_save, \
         patch("core.worker.whatsapp.send_reply", return_value=True) as mock_send, \
         patch("core.worker.db.mark_reply_sent"), \
         patch("core.worker.db.append_conversation_turn"), \
         patch("core.worker.db.mark_history_appended"), \
         patch("core.worker.db.succeed_message_job") as mock_succeed, \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job(result=stored))

    mock_bot.assert_not_called()
    mock_save.assert_not_called()
    mock_send.assert_called_once_with("79991234567", "Сохранённый ответ")
    mock_succeed.assert_called_once()


def test_process_job_post_send_recovery_no_resend_no_duplicate_history():
    """Post-send recovery edge: reply_sent=True and history_appended=True (the
    append succeeded but succeed_message_job previously failed, leaving the job
    'processing' to be reclaimed). The retry must NOT re-send and must NOT append
    the conversation turn again — it just reaches a terminal state."""
    stored = _result(reply="Уже отправлено")
    job = _job(result=stored)
    job["reply_sent"] = True
    job["history_appended"] = True
    with patch("core.worker.whatsapp.send_reply") as mock_send, \
         patch("core.worker.db.mark_reply_sent") as mock_mark, \
         patch("core.worker.db.append_conversation_turn") as mock_append, \
         patch("core.worker.db.succeed_message_job") as mock_succeed, \
         patch("core.worker.db.fail_message_job") as mock_fail, \
         patch("core.worker.db.record_message_event"):

        worker.process_job(job)

    mock_send.assert_not_called()
    mock_mark.assert_not_called()
    mock_append.assert_not_called()
    mock_succeed.assert_called_once_with("wamid.1")
    mock_fail.assert_not_called()


# --- process_job: handle_message raises -> failure path ---

def test_process_job_fails_when_handle_message_raises():
    with patch("core.worker.bot.handle_message", side_effect=Exception("openai down")), \
         patch("core.worker.whatsapp.send_reply") as mock_send, \
         patch("core.worker.db.succeed_message_job") as mock_succeed, \
         patch("core.worker.db.fail_message_job", return_value="failed") as mock_fail, \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job())

    mock_send.assert_not_called()
    mock_succeed.assert_not_called()
    mock_fail.assert_called_once()


# --- notifications ---

def test_process_job_sends_owner_alert_for_complete_booking():
    with patch("core.worker.bot.handle_message", return_value=_complete_booking_result()), \
         patch("core.worker.db.save_job_result"), \
         patch("core.worker.whatsapp.send_reply", return_value=True), \
         patch("core.worker.db.mark_reply_sent"), \
         patch("core.worker.db.append_conversation_turn"), \
         patch("core.worker.db.mark_history_appended"), \
         patch("core.worker.db.claim_booking_alert", return_value=True), \
         patch("core.worker.db.finish_booking_alert") as mock_finish, \
         patch("core.worker.notify.send_owner_alert") as mock_owner, \
         patch("core.worker.db.succeed_message_job"), \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job())

    mock_owner.assert_called_once_with(
        "79991234567", "whatsapp",
        {"guest_name": "Айгуль", "check_in": "2026-06-05", "check_out": "2026-06-07", "num_guests": 2},
    )
    # Claimed alert closed out as sent.
    mock_finish.assert_called_once_with(
        "whatsapp", "79991234567",
        {"guest_name": "Айгуль", "check_in": "2026-06-05", "check_out": "2026-06-07", "num_guests": 2},
        True,
    )


def test_process_job_no_owner_alert_when_already_alerted():
    with patch("core.worker.bot.handle_message", return_value=_complete_booking_result()), \
         patch("core.worker.db.save_job_result"), \
         patch("core.worker.whatsapp.send_reply", return_value=True), \
         patch("core.worker.db.mark_reply_sent"), \
         patch("core.worker.db.append_conversation_turn"), \
         patch("core.worker.db.mark_history_appended"), \
         patch("core.worker.db.claim_booking_alert", return_value=False), \
         patch("core.worker.notify.send_owner_alert") as mock_owner, \
         patch("core.worker.db.succeed_message_job"), \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job())

    mock_owner.assert_not_called()


def test_process_job_sends_escalation_alert():
    with patch("core.worker.bot.handle_message",
               return_value=_result(reply="Передаю администратору.", escalated=True, persist_history=False)), \
         patch("core.worker.db.save_job_result"), \
         patch("core.worker.whatsapp.send_reply", return_value=True) as mock_send, \
         patch("core.worker.db.mark_reply_sent"), \
         patch("core.worker.db.append_conversation_turn") as mock_append, \
         patch("core.worker.notify.send_escalation_alert") as mock_esc, \
         patch("core.worker.db.succeed_message_job"), \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job())

    mock_send.assert_called_once_with("79991234567", "Передаю администратору.")
    mock_esc.assert_called_once_with("79991234567", "whatsapp")
    mock_append.assert_not_called()  # over-limit replies are not stored in history


def test_process_job_succeeds_even_if_owner_alert_fails():
    """Notification failures are non-fatal: the reply is already delivered, so the
    job must NOT be retried (which would re-send the reply)."""
    with patch("core.worker.bot.handle_message", return_value=_complete_booking_result()), \
         patch("core.worker.db.save_job_result"), \
         patch("core.worker.whatsapp.send_reply", return_value=True), \
         patch("core.worker.db.mark_reply_sent"), \
         patch("core.worker.db.append_conversation_turn"), \
         patch("core.worker.db.mark_history_appended"), \
         patch("core.worker.db.claim_booking_alert", return_value=True), \
         patch("core.worker.db.finish_booking_alert") as mock_finish, \
         patch("core.worker.notify.send_owner_alert", side_effect=Exception("network")), \
         patch("core.worker.db.succeed_message_job") as mock_succeed, \
         patch("core.worker.db.fail_message_job") as mock_fail, \
         patch("core.worker.db.record_message_event"):

        worker.process_job(_job())

    # Alert send failed -> closed out as 'failed' (re-claimable), NOT 'sent'.
    mock_finish.assert_called_once_with(
        "whatsapp", "79991234567",
        {"guest_name": "Айгуль", "check_in": "2026-06-05", "check_out": "2026-06-07", "num_guests": 2},
        False,
    )
    mock_succeed.assert_called_once()
    mock_fail.assert_not_called()


# --- run_once ---

def test_run_once_returns_false_when_queue_empty():
    with patch("core.worker.db.claim_message_job", return_value=None), \
         patch("core.worker.process_job") as mock_process:
        worked = worker.run_once()
    assert worked is False
    mock_process.assert_not_called()


def test_run_once_processes_claimed_job():
    job = _job()
    with patch("core.worker.db.claim_message_job", return_value=job), \
         patch("core.worker.process_job") as mock_process:
        worked = worker.run_once()
    assert worked is True
    mock_process.assert_called_once_with(job)
