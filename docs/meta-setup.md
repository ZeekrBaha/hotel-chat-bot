# Meta WhatsApp Business API Setup

## Prerequisites
- A Facebook account (personal is fine)
- A phone number that is NOT currently registered on WhatsApp
  (can be the hotel's SIM card — registering it here will remove it from regular WhatsApp)
- A business name (even informal — "Hotel Bot" works)

## Step 1: Create Meta Business Manager
1. Go to https://business.facebook.com
2. Click "Create account"
3. Enter business name, your name, business email
4. Complete the form — you can use the hotel's address

**After submitting:** Meta may ask you to verify your business by uploading a document
(e.g., a business registration certificate or utility bill with your business name).
This review takes **1–3 business days**. You cannot proceed to production until it's approved.
You can continue with testing using Meta's free test number while you wait.

## Step 2: Create a Facebook Developer App
1. Go to https://developers.facebook.com/apps
2. Click "Create App"
3. Select "Business" as the app type
4. Give it any name (e.g., "Hotel Bot")
5. Connect it to your Business Manager account from Step 1

## Step 3: Add WhatsApp Product
1. Inside your app → "Add a Product" → find "WhatsApp" → click "Set up"
2. You'll see the WhatsApp Getting Started page
3. Meta gives you a free test number for testing — use whatever number is shown on your Getting Started page
   (Meta's test number may change — always use what's shown on your page, not a hardcoded number)

## Step 4: Get Your Access Token

> ⚠️ **IMPORTANT:** Meta gives you a temporary token that **expires after 24 hours**.
> If you use this token in n8n, the bot will stop working the next day.
> You MUST generate a permanent token for the bot to work long-term.

**To generate a permanent token:**
1. In Meta Business Settings → Users → System Users → click "Add"
2. Give the system user any name (e.g., "Hotel Bot User") and set role to "Admin"
3. Click "Add Assets" → Apps → select your app → give "Full control"
4. Click "Generate New Token" → select your app → check the permission "whatsapp_business_messaging"
5. Copy the token — **save it somewhere safe, it won't be shown again**

**Also copy from the WhatsApp Getting Started page:**
- **Phone Number ID** (looks like: 123456789012345)
- **WhatsApp Business Account ID**

## Step 5: Add the Hotel's Real Phone Number
1. WhatsApp → Getting Started → "Add phone number"
2. Enter the hotel's phone number
3. Verify via SMS or voice call OTP
4. This number is now your WhatsApp Business number

## Step 6: Configure the Webhook
The webhook connects Meta to your n8n bot. You need your n8n URL first (set up n8n before this step).

1. WhatsApp → Configuration → Webhook → click "Edit"
2. **Callback URL:** Your n8n webhook URL
   - Open n8n → your "Hotel WhatsApp Bot" workflow → click the "WhatsApp Trigger" node
   - Copy the Webhook URL shown at the bottom of that node
   - Paste it here
3. **Verify Token:** `hotel-bot-verify-2026`
4. Click "Verify and save"
5. Under "Webhook fields" → click "Subscribe" next to "messages"

## Credentials to save (fill in as you complete each step)

```
PHONE_NUMBER_ID=_______________
WHATSAPP_ACCESS_TOKEN=_______________   ← permanent token from Step 4
VERIFY_TOKEN=hotel-bot-verify-2026
SISTER_WHATSAPP_NUMBER=+996XXXXXXXXX   ← sister's personal WhatsApp number (with country code)
```

## Estimated Time
- Account creation: 30 minutes
- Business verification: 1–3 business days (Meta reviews documents)
- Permanent token setup: 15 minutes
- Phone number setup: 15 minutes
- Webhook config: 5 minutes (after n8n is running)

## What's Next
Once you have your Phone Number ID and permanent Access Token, go to your n8n instance and enter them as credentials in the "WhatsApp Business Cloud" credential — this is covered in the n8n workflow setup step.
