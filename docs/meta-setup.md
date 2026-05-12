# Meta WhatsApp Business API Setup

## Prerequisites
- A Facebook account (personal is fine)
- A phone number that is NOT currently registered on WhatsApp
  (can be the hotel's SIM card — you'll lose the regular WhatsApp on it)
- A business name (even informal — "Отель Иссык-Куль" works)

## Step 1: Create Meta Business Manager
1. Go to https://business.facebook.com
2. Click "Create account"
3. Enter business name, your name, business email
4. Complete the form — you can use the hotel's address

## Step 2: Create a Facebook Developer App
1. Go to https://developers.facebook.com/apps
2. Click "Create App"
3. Select "Business" as the app type
4. Give it any name (e.g., "Hotel Bot")
5. Connect it to your Business Manager account

## Step 3: Add WhatsApp Product
1. Inside your app → "Add a Product" → find "WhatsApp" → click "Set up"
2. You'll see the WhatsApp Getting Started page
3. Meta gives you a free test number (+1 415 523 8886) to start — use this for testing

## Step 4: Get Your Credentials (save these — you'll need them in n8n)
From the WhatsApp Getting Started page, copy:
- **Phone Number ID** (looks like: 123456789012345)
- **WhatsApp Business Account ID**
- **Temporary Access Token** (click "Generate" — valid 24hrs for testing)

For production, generate a permanent token:
1. Business Settings → System Users → Add → give it "Admin" role
2. Add assets → Apps → your app → Full control
3. Generate token → select your app → check "whatsapp_business_messaging" permission
4. Copy the permanent token

## Step 5: Add a Real Phone Number (for production)
1. WhatsApp → Getting Started → "Add phone number"
2. Enter the hotel's phone number
3. Verify via SMS or voice call OTP
4. This number is now your WhatsApp Business number

## Step 6: Configure Webhook (fill in after n8n is deployed — Task 4)
1. WhatsApp → Configuration → Webhook
2. Callback URL: [your n8n webhook URL — you'll get this in Task 4]
3. Verify Token: choose any secret string, e.g., "hotel-bot-verify-2026"
4. Subscribe to: "messages"
5. Click "Verify and save"

## Credentials to save (fill in as you complete each step)

```
PHONE_NUMBER_ID=_______________
WHATSAPP_ACCESS_TOKEN=_______________
VERIFY_TOKEN=hotel-bot-verify-2026
SISTER_WHATSAPP_NUMBER=+996XXXXXXXXX
```

## Estimated Time
- Account creation: 30 minutes
- Business verification: 1–3 business days (Meta reviews it)
- Phone number setup: 15 minutes
- Webhook config: 5 minutes (after n8n is running)
