# Hotel WhatsApp Bot — Owner Guide

This bot automatically answers guest questions on WhatsApp so you don't have to reply to every message.

## What the bot does automatically
- Answers questions about prices, check-in/out time, directions, amenities
- Collects booking requests (name, dates, number of guests)
- Sends you a WhatsApp notification when someone wants to book

## What you still do
- Confirm the booking by replying to the guest
- Send the payment QR code to the guest
- Answer anything the bot can't handle (it will say "I'll check with the administrator")

## How to update hotel information
1. Open the file `system-prompt.txt` in any text editor
2. Change the information between the `[ ]` brackets
3. In n8n → open "Hotel WhatsApp Bot" workflow → click "Claude — FAQ Reply" node → paste the updated system-prompt.txt content into "System Prompt" → do the same for "Claude — Booking Intake" → save → workflow stays active

## If the bot stops responding
1. Go to your n8n URL (saved in docs/meta-setup.md)
2. Log in with your email and password
3. Check "Executions" tab — it shows the last messages received and any errors
4. If the workflow shows "Inactive" — click the toggle to activate it

## Monthly costs
- n8n hosting (PikaPods): ~$5–7/month (charged to your card)
- AI (Anthropic Claude): ~$1–2/month (check usage at https://console.anthropic.com)
- WhatsApp: free for messages guests send to you

## Files in this project
- `system-prompt.txt` — the bot's instructions and hotel information (edit this to update the bot)
- `test-messages.txt` — test script to verify the bot is working correctly
- `n8n-workflow.json` — the bot workflow (import into n8n if you need to restore it)
- `docs/meta-setup.md` — step-by-step guide for the WhatsApp Business API setup

## Contact for technical help
[Developer contact — fill in]

## Phase 2: Voice calls (future)
When ready to handle phone calls automatically, see the Phase 2 section in the implementation plan.
