"""Durable queue worker.

Drains the message_jobs queue created by inbound webhooks. Run as a standalone
process, separate from the web tier:

    python -m core.worker

The web process only enqueues; all OpenAI / WhatsApp / Supabase work happens here,
so a transient failure retries (up to max_retries) instead of dropping the guest's
message. Concurrency is bounded by WORKER_CONCURRENCY, which provides backpressure.
"""
import datetime
import hashlib
import logging
import os
import signal
import threading
import time
from threading import Thread

from core import bot, db, notify
from platforms import whatsapp

_logger = logging.getLogger(__name__)
_stop = threading.Event()

_BOOKING_FIELDS = ("guest_name", "check_in", "check_out", "num_guests")


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:8]


def _booking_complete(result: dict) -> bool:
    try:
        datetime.date.fromisoformat(result["check_in"])
        datetime.date.fromisoformat(result["check_out"])
    except (TypeError, ValueError, KeyError):
        return False
    return (
        bool(result.get("guest_name"))
        and isinstance(result.get("num_guests"), int)
        and result["num_guests"] > 0
    )


def _dispatch_notifications(message_id: str, platform: str, sender_id: str,
                            result: dict, phone_hash: str) -> None:
    """Fire owner / escalation alerts. NON-FATAL: the guest's reply is already
    delivered, so a notification failure is logged and recorded but must never
    fail the job (which would re-send the reply on retry)."""
    if result.get("escalated"):
        try:
            notify.send_escalation_alert(sender_id, platform)
            db.record_message_event(message_id, "notify_result", {"kind": "escalation", "ok": True})
        except Exception as e:
            _logger.exception("escalation_alert_failed phone=%s", phone_hash)
            db.record_message_event(message_id, "notify_result",
                                    {"kind": "escalation", "ok": False, "error": repr(e)})
        return

    if _booking_complete(result):
        booking = {field: result[field] for field in _BOOKING_FIELDS}
        # Atomic claim: only one worker wins, so parallel jobs for the same booking
        # cannot both send. A non-claim means another worker already handled (or is
        # handling) it.
        if db.claim_booking_alert(platform, sender_id, booking):
            try:
                notify.send_owner_alert(sender_id, platform, booking)
                db.finish_booking_alert(platform, sender_id, booking, True)
                db.record_message_event(message_id, "notify_result", {"kind": "owner_alert", "ok": True})
            except Exception as e:
                # Mark 'failed' so the alert can be re-claimed and retried later
                # (a failed send must not permanently suppress the alert).
                _logger.exception("owner_alert_failed phone=%s", phone_hash)
                db.finish_booking_alert(platform, sender_id, booking, False)
                db.record_message_event(message_id, "notify_result",
                                        {"kind": "owner_alert", "ok": False, "error": repr(e)})


def process_job(job: dict) -> None:
    """Process one claimed job: generate (or reuse) reply, send, persist history,
    notify, and mark the job replied. On any failure, hand off to fail_message_job
    which retries or dead-letters."""
    message_id = job["message_id"]
    platform = job["platform"]
    sender_id = job["sender_id"]
    text = job["text"]
    phone_hash = _hash_phone(sender_id)
    t0 = time.monotonic()

    result = job.get("result")
    already_sent = bool(job.get("reply_sent"))

    if already_sent:
        # Reclaimed after a previous attempt already confirmed delivery (e.g. the
        # worker crashed before marking the job replied). Do NOT re-send — that
        # would double-message the guest. Just finish the best-effort post-send work.
        _logger.warning("reply_already_sent_skipping_resend message_id=%s phone=%s",
                        message_id, phone_hash)
        db.record_message_event(message_id, "resend_skipped", {"reason": "reply_sent"})
        result = result or {}
    else:
        # Phase 1: generate (once) + send. A failure here is safe to retry,
        # because the guest has not received anything yet.
        try:
            if result is None:
                # First attempt: generate the reply and persist it so a later retry
                # re-sends the same reply instead of re-calling OpenAI.
                db.record_message_event(message_id, "inbound",
                                        {"platform": platform, "phone": phone_hash, "len": len(text)})
                result = bot.handle_message(platform, sender_id, text)
                db.save_job_result(message_id, result)
                db.record_message_event(message_id, "reply_generated", {
                    "booking_intent": result.get("is_booking_intent"),
                    "escalated": result.get("escalated"),
                })

            send_ok = whatsapp.send_reply(sender_id, result["reply"])
            db.record_message_event(message_id, "send_result", {"ok": send_ok})
            if not send_ok:
                raise RuntimeError("whatsapp send_reply returned False")
            # Record delivery immediately so a reclaim never re-sends this reply.
            db.mark_reply_sent(message_id)
        except Exception as e:
            _logger.exception("job_failed message_id=%s phone=%s", message_id, phone_hash)
            try:
                status = db.fail_message_job(message_id, repr(e))
                if status == "dead":
                    _logger.error("job_dead_letter message_id=%s phone=%s", message_id, phone_hash)
            except Exception:
                _logger.exception("fail_message_job_failed message_id=%s", message_id)
            return

    # Phase 2: post-send. The reply IS delivered, so everything below is
    # best-effort — a failure here must never retry the job (that would re-send
    # the reply to the guest).
    if result.get("persist_history") and not job.get("history_appended"):
        try:
            db.append_conversation_turn(platform, sender_id, [
                {"role": "user", "content": text},
                {"role": "assistant", "content": result["reply"]},
            ])
            # Flag it so a reclaim after a failed succeed_message_job does not
            # append the same turn twice.
            db.mark_history_appended(message_id)
        except Exception:
            _logger.exception("append_history_failed message_id=%s", message_id)

    try:
        _dispatch_notifications(message_id, platform, sender_id, result, phone_hash)
    except Exception:
        _logger.exception("dispatch_notifications_failed message_id=%s", message_id)

    try:
        db.succeed_message_job(message_id)
    except Exception:
        _logger.exception("succeed_message_job_failed message_id=%s", message_id)

    latency_ms = int((time.monotonic() - t0) * 1000)
    _logger.info("job_replied message_id=%s phone=%s booking=%s latency_ms=%d",
                 message_id, phone_hash, result.get("is_booking_intent"), latency_ms)


def run_once() -> bool:
    """Claim and process a single job. Returns True if a job was processed,
    False if the queue was empty."""
    job = db.claim_message_job()
    if job is None:
        return False
    process_job(job)
    return True


def _worker_loop(poll_interval: float) -> None:
    while not _stop.is_set():
        try:
            worked = run_once()
        except Exception:
            _logger.exception("worker_loop_error")
            worked = False
        if not worked:
            _stop.wait(poll_interval)


def _handle_signal(signum, frame) -> None:
    _logger.info("signal %s received, draining workers...", signum)
    _stop.set()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    num_workers = int(os.environ.get("WORKER_CONCURRENCY", "4"))
    poll_interval = float(os.environ.get("WORKER_POLL_INTERVAL", "1.0"))
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _logger.info("worker starting concurrency=%d poll_interval=%.1fs", num_workers, poll_interval)
    threads = [
        Thread(target=_worker_loop, args=(poll_interval,), name=f"worker-{i}", daemon=False)
        for i in range(num_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _logger.info("worker stopped")


if __name__ == "__main__":
    main()
