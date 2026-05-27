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
systemctl restart hotel-chat-bot
```

Перезапуск занимает несколько секунд.

---

## Если бот перестал отвечать

```bash
ssh hotelbot@ВАШ_IP
systemctl status hotel-chat-bot              # запущен ли
journalctl -u hotel-chat-bot -n 100          # последние логи
systemctl restart hotel-chat-bot             # перезапустить
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
├── app.py                  ← Flask: webhook routes + SIGTERM graceful shutdown
├── core/
│   ├── bot.py              ← OpenAI gpt-4o-mini + structured JSON output
│   ├── db.py               ← Supabase RPCs (atomic increment / dedup / history)
│   └── notify.py           ← Owner alerts via Meta Graph API v19.0
├── platforms/
│   └── whatsapp.py         ← parse, HMAC verify, send reply (Telegram — v2)
├── sql/
│   ├── schema.sql          ← Tables + 4 atomic PostgreSQL RPCs
│   └── retention.sql       ← pg_cron schedule, 90-day conversation TTL
├── tests/                  ← 64 pytest cases
├── deploy/
│   ├── hotel-chat-bot.service   ← systemd unit, gunicorn 4×gthread
│   └── nginx.conf               ← TLS reverse proxy + rate limiting
└── system-prompt.txt       ← Hotel data + bot rules (Russian/Kyrgyz)
```

**Stack:** Python 3.13 · Flask 3 · OpenAI 2.38 · Supabase 2.4 · gunicorn (4 workers × 4 threads) · nginx + Let's Encrypt.

## How it works

1. Meta → `POST /whatsapp/webhook` (HMAC-SHA256 signed)
2. Verify signature → `parse_inbound` → `mark_message_processed` (Supabase RPC dedup)
3. Spin off background thread, ack 200 immediately (stays inside Meta's 20s window)
4. Thread: read history → call `gpt-4o-mini` with strict JSON schema → parse `{reply, is_booking_intent, guest_name, check_in, check_out, num_guests}`
5. Send reply to guest → atomically append turn via `append_conversation_turn` RPC
6. If all 4 booking slots are valid ISO dates + non-empty → `set_booking_alert_if_new` RPC (dedupes per booking key) → owner alert
7. Track in-flight threads, drain on SIGTERM with up to 15 s join + chain to gunicorn's handler

## Production-readiness

The codebase went through **6 cycles of code review**. Every defect found is tracked in:

- `improvements.md` — v1 baseline (25 items)
- `proposalstoenhance.md` — patterns borrowed from `wassengerhq/whatsapp-chatgpt-bot`
- `wassenger-review.md` — audit of that reference repo
- `improvements-v2.md` through `improvements-v6.md` — successive fix waves

**Final state: 150 items reviewed, 145 closed, 2 operational carryovers, 4 micro-observations.**

What's already in: async ack, message-ID dedup, HMAC verification, env validation at startup, structured output for booking intent, daily message ceiling with escalation, group-chat filter, input cap, ISO-8601 slot validation, atomic SQL RPCs for all state mutations, owner-alert deduplication, graceful SIGTERM, structured logging, `/health/deep`, GH Actions CI with pinned ruff, nginx rate limit, retention via pg_cron.

What's still owed before unattended ops:
1. Postgres service container in CI for RPC smoke tests
2. Meta WhatsApp template approval for `owner_booking_alert` (24 h Meta approval clock — required for owner notifications outside the 24-hour customer-service window)

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env       # fill in real values
pytest -q                  # 64 tests, ~0.5s
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

# Install systemd unit + nginx config:
sudo cp deploy/hotel-chat-bot.service /etc/systemd/system/
sudo cp deploy/nginx.conf /etc/nginx/sites-available/hotel-chat-bot
sudo ln -s /etc/nginx/sites-available/hotel-chat-bot /etc/nginx/sites-enabled/
sudo systemctl enable --now hotel-chat-bot
sudo nginx -t && sudo systemctl reload nginx
```

WhatsApp setup (Meta Business Manager): see [`docs/meta-setup.md`](docs/meta-setup.md).

## License

MIT.
