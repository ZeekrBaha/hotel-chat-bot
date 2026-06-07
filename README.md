# Hotel Chat Bot

> WhatsApp-бот для небольшого отеля. Автоматически отвечает гостям на русском и кыргызском, собирает заявки на бронирование, отправляет уведомления владельцу. Python + Flask + OpenAI + Supabase. Работает на VPS 24/7.

**Контакт разработчика:** _[Имя / телефон / email — заполнить]_

---

## Что делает бот

**Автоматически:**
- Отвечает на вопросы о ценах, заезде/выезде, удобствах, как добраться
- Определяет язык гостя (русский / кыргызский) и отвечает на нём же
- Помнит историю переписки с каждым гостем (последние 20 сообщений)
- Собирает данные для бронирования: имя, даты заезда/выезда, количество гостей
- Отправляет владельцу уведомление в WhatsApp, когда у гостя есть все 4 поля брони
- Знает сегодняшнюю дату — корректно понимает «завтра», «послезавтра»
- Останавливается, если один гость отправил больше 50 сообщений за сутки, и пингует владельца

**Не делает:**
- Не подтверждает бронирование сам — владелец делает это вручную
- Никогда не раскрывает реквизиты для оплаты — только администратор
- Не отвечает в групповых чатах (фильтрует автоматически)
- Не обрабатывает голосовые/изображения/видео — просит прислать текст

---

## Как обновить информацию об отеле

Все данные хранятся в одном файле — `system-prompt.txt` на сервере. Заполните `[НАЗВАНИЕ ОТЕЛЯ]`, `[АДРЕС]`, цены и т.д.

```bash
ssh hotelbot@ВАШ_IP
nano system-prompt.txt
# Сохранить: Ctrl+O → Enter → Ctrl+X
systemctl restart hotel-chat-bot-worker   # бот-обработчик читает system-prompt.txt
```

Перезапуск занимает несколько секунд.

---

## Если бот перестал отвечать

```bash
ssh hotelbot@ВАШ_IP
# Два сервиса: hotel-chat-bot (приём сообщений) и hotel-chat-bot-worker (ответы).
systemctl status hotel-chat-bot hotel-chat-bot-worker     # запущены ли оба
journalctl -u hotel-chat-bot-worker -n 100                # логи обработчика (ответы/ошибки)
systemctl restart hotel-chat-bot hotel-chat-bot-worker    # перезапустить оба
```

Бот запускается автоматически при перезагрузке сервера. Если уведомления владельцу перестали приходить — проверьте, написали ли вы боту хоть одно сообщение за последние 24 часа (это требование Meta).

---

## Ежемесячные расходы

| Сервис | Стоимость |
|---|---|
| VPS (Hetzner) | ~€4 / месяц |
| Домен (опционально) | ~$1 / месяц |
| OpenAI GPT-4o mini | ~$0.25–0.75 / месяц |
| WhatsApp Business API | бесплатно (входящие сообщения) |
| Supabase (база данных) | бесплатно |
| **Итого** | **~$6–7 / месяц** |

Модель `gpt-4o-mini`: $0.15 за 1M входящих токенов, $0.60 за 1M исходящих.

| Объём | Стоимость ИИ |
|---|---|
| 500 сообщений / мес (низкий сезон) | ~$0.12 |
| 1 000 сообщений / мес | ~$0.24 |
| 3 000 сообщений / мес (высокий сезон) | ~$0.72 |

На реалистичном объёме для небольшого отеля — **до $1/месяц**.

---

# Для разработчика

## Архитектура

```
hotel-chat-bot/
├── app.py                  ← Flask webhook: verify + enqueue only (no processing)
├── core/
│   ├── bot.py              ← OpenAI gpt-4o-mini + structured JSON output
│   ├── worker.py           ← durable-queue worker: drains jobs, retries, events
│   ├── db.py               ← Supabase RPCs (counter / queue / history / events)
│   └── notify.py           ← Owner alerts via Meta Graph API (text or template)
├── platforms/
│   └── whatsapp.py         ← parse, HMAC verify, send reply (Telegram — v2)
├── sql/
│   ├── schema.sql          ← conversations + message_jobs/message_events + RPCs
│   └── retention.sql       ← pg_cron schedule: conversation / queue / event TTL
├── tests/
│   ├── test_*.py           ← unit tests (mocked Supabase/OpenAI/WhatsApp)
│   └── integration/        ← RPC tests against a real Postgres (DATABASE_URL)
├── deploy/
│   ├── hotel-chat-bot.service         ← web (gunicorn), enqueues
│   ├── hotel-chat-bot-worker.service  ← queue worker (python -m core.worker)
│   └── nginx.conf                     ← TLS reverse proxy + rate limiting
└── system-prompt.txt       ← Hotel data + bot rules (Russian/Kyrgyz)
```

**Stack:** Python 3.13 · Flask 3 · OpenAI 2.38 · Supabase 2.4 · gunicorn · nginx + Let's Encrypt.

## How it works

The web tier and the processing tier are split by a **durable Postgres-backed queue**
(`message_jobs`), so a failed OpenAI / WhatsApp / Supabase call retries instead of
silently dropping the guest's message.

**Web (`app.py`)** — fast, does no real work:
1. Meta → `POST /whatsapp/webhook` (HMAC-SHA256 signed)
2. Verify signature → `parse_inbound` → cap text at 1000 chars
3. `enqueue_message` RPC (PK on `message_id` dedups Meta retries atomically) → ack 200

**Worker (`core/worker.py`)** — one or more processes draining the queue:
1. `claim_message_job` RPC (`FOR UPDATE SKIP LOCKED`) atomically claims one job and marks it `processing`
2. First attempt: read history → `gpt-4o-mini` with strict JSON schema → `{reply, is_booking_intent, guest_name, check_in, check_out, num_guests}`. The result is **persisted on the job**, so a retry re-sends the same reply (no duplicate OpenAI cost, no double counting)
3. `send_reply` to the guest — **only after a successful send** is the turn appended to history via `append_conversation_turn`
4. If all 4 booking slots are valid ISO dates + non-empty → `claim_booking_alert` (atomic per-key claim — exactly one worker wins, so parallel jobs can't double-alert) → owner alert → `finish_booking_alert` (`sent` on success, `failed`/re-claimable on failure). Notification failures are non-fatal (the reply already landed)
5. `succeed_message_job` (→ `replied`). Any exception **before** a confirmed send → `fail_message_job`, which retries up to `max_retries` (→ `failed`) then dead-letters (→ `dead`)

Every step records an audit event (inbound / reply_generated / send_result / notify_result)
in `message_events`. Concurrency is bounded by `WORKER_CONCURRENCY`, which provides backpressure.

### Delivery guarantee

Outbound delivery is **at-least-once**, not exactly-once. On a successful send the
worker immediately sets `reply_sent`, and a reclaimed (stale) job with `reply_sent =
TRUE` skips the resend — so a crash *after* a confirmed send does not double-message
the guest. The only remaining duplicate window is a crash in the brief gap between
WhatsApp returning 200 and `mark_reply_sent` committing; this is inherent to calling
an external API and is preferred over the old behaviour (silent message loss).
Retries re-use the persisted reply, so a retry never re-calls OpenAI or re-counts the
message — but it can re-send if that narrow window is hit.

Two more flags make post-send recovery safe: `history_appended` stops a reclaim from
appending the same conversation turn twice, and owner alerts use an **atomic claim**
(`claim_booking_alert` → send → `finish_booking_alert`). The single-row-lock claim
guarantees exactly one worker sends, even with parallel jobs for the same booking;
a failed send is marked `failed` (re-claimable, not suppressed) and a `sending` claim
whose worker died is reclaimed after a stale timeout.

### Health endpoints

| Endpoint | Checks | Use |
|---|---|---|
| `/health` | process up | liveness; cheap, probe freely |
| `/health/ready` | Supabase (one indexed query) | readiness for the load balancer — no OpenAI call |
| `/health/deep` | Supabase + a live OpenAI call | diagnostics for humans / monitoring; heavier |

### Queue monitoring

`schema.sql` ships SQL views (query them from the Supabase dashboard):

```sql
SELECT * FROM queue_status;      -- job counts per status
SELECT * FROM dead_letter_jobs;  -- retries exhausted, need a human
SELECT * FROM stuck_jobs;        -- 'processing' > 5 min (worker likely died)
```

### Configuration

Required env vars are validated at web startup (see the `REQUIRED_ENV` list in `app.py`).
Optional:

| Var | Default | Purpose |
|---|---|---|
| `WORKER_CONCURRENCY` | `4` | worker threads (backpressure bound) |
| `WORKER_POLL_INTERVAL` | `1.0` | seconds to wait when the queue is empty |
| `OWNER_ALERT_TEMPLATE` | _(unset)_ | approved WhatsApp template for owner booking alerts; falls back to free-form text when unset |
| `ESCALATION_ALERT_TEMPLATE` | _(unset)_ | template for escalation alerts; text fallback when unset |
| `WHATSAPP_TEMPLATE_LANG` | `ru` | language code for the templates above |

Templates let owner alerts deliver **outside** Meta's 24-hour customer-service window.
Without them, alerts only arrive if the owner messaged the bot in the last 24 h.

Add the optional vars to your `.env` (and to `.env.example` for the next deployer):

```dotenv
# WORKER_CONCURRENCY=4
# WORKER_POLL_INTERVAL=1.0
# OWNER_ALERT_TEMPLATE=owner_booking_alert
# ESCALATION_ALERT_TEMPLATE=owner_escalation_alert
# WHATSAPP_TEMPLATE_LANG=ru
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres  # integration tests only
```

## Production-readiness

This is a working prototype, not a turnkey production deployment — the heavy
operational risks (silent message loss, free-form-text owner alerts, shallow health
checks, untested SQL) are addressed, but it still needs real-traffic hardening and
the manual Meta steps below.

What's in: durable Postgres queue with retry + dead-letter, retries that reuse the
same generated reply (no duplicate OpenAI cost; outbound WhatsApp delivery is
at-least-once — see the note below), history written only after a confirmed send, HMAC verification, env validation at
startup, structured booking output, daily message ceiling with escalation,
group-chat filter, input cap, ISO-8601 slot validation, atomic SQL RPCs, owner-alert
dedup, optional WhatsApp templates, per-message audit events, real `/health/deep`
(live Supabase query + OpenAI call), GH Actions CI with pinned ruff **and a Postgres
service container running the RPC integration tests**, nginx rate limit, retention via pg_cron.

Manual steps still owed before unattended ops:
1. Get the Meta WhatsApp templates approved (`owner_booking_alert`, `owner_escalation_alert`) and set `OWNER_ALERT_TEMPLATE` / `ESCALATION_ALERT_TEMPLATE` (24 h Meta approval clock)
2. Deploy and supervise the worker service alongside the web service (see Deployment)

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env       # fill in real values
pytest -q                  # 83 unit tests (integration skipped without DATABASE_URL)

# Optional: run the SQL RPC integration tests against a throwaway Postgres
docker run -d --rm -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest -q  # 94 tests
```

## Deployment

```bash
# On the VPS, as user `hotelbot`:
git clone <this repo>
cd hotel-chat-bot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in WHATSAPP_*, OPENAI_API_KEY, SUPABASE_*

# Bootstrap database (in Supabase SQL editor):
#   1. Run sql/schema.sql once
#   2. Run sql/retention.sql once to schedule daily cleanup via pg_cron

# Install systemd units (web + worker) + nginx config:
sudo cp deploy/hotel-chat-bot.service /etc/systemd/system/
sudo cp deploy/hotel-chat-bot-worker.service /etc/systemd/system/
sudo cp deploy/nginx.conf /etc/nginx/sites-available/hotel-chat-bot
sudo ln -s /etc/nginx/sites-available/hotel-chat-bot /etc/nginx/sites-enabled/
sudo systemctl enable --now hotel-chat-bot hotel-chat-bot-worker
sudo nginx -t && sudo systemctl reload nginx
```

WhatsApp setup (Meta Business Manager): see [`docs/meta-setup.md`](docs/meta-setup.md).

## License

MIT.
