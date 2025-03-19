# WhatsApp Integration

This document provides information on how to set up and use the WhatsApp integration for wallet transaction tracking.

## Overview

The WhatsApp integration allows users to send transaction information via WhatsApp messages. This makes it easy to record expenses, incomes, and transfers (soon) on the go.

## Setup Requirements

1. [Meta for Developers account](https://developers.facebook.com/)
2. [A WhatsApp Business API account](https://developers.facebook.com/apps/)
3. A verified phone number for your WhatsApp Business account. ([This might help](https://stackoverflow.com/a/79216401))

## Server Configuration

1. Set the following environment variables in your `.env` file:
   ```
   WHATSAPP_ACCESS_TOKEN=your-access-token
   WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
   WHATSAPP_VERIFY_TOKEN=your-webhook-verify-token
   WHATSAPP_API_VERSION=vXX.X
   ```

## User Configuration

Each user needs to register their phone number to use the WhatsApp integration:

1. In the user profile, update your WhatsApp phone number
2. Ensure the phone number is in international format (e.g., +1234567890)
3. Test your integration by sending a message to the WhatsApp Business number

## Message Format

Users can send messages in the following formats:

### Expenses
```
expense 100 groceries at supermarket
```
or simply (defaults to expense):
```
100 groceries at supermarket
```

### Incomes
```
income 500 salary
```

### Transfers (Not fully implemented yet)
```
transfer 200 from savings to checking
```


## Webhook Configuration

To set up the WhatsApp webhooks:

1. Go to your Meta for Developers dashboard
2. Navigate to your WhatsApp app
3. Configure a webhook with the following URL:
   ```
   https://your-domain.com/api/v1/whatsapp/webhook
   ```
4. Use the `WHATSAPP_VERIFY_TOKEN` you set in the .env file
5. Subscribe to the `messages` webhook field

### Development Webhook

To set up the development webhook, follow these steps:

1. Install [cloudflared Quick Tunnel tool](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/):
   ```
   brew install cloudflared
   ```
2. Run the following command:
   ```
   cloudflared tunnel --url http://localhost:9000
   ```
3. Use the generated URL as your webhook URL in the Meta for Developers dashboard

## Testing the Integration

1. Complete the server and webhook configuration
2. Register your phone number in the app
3. Send a test message to your WhatsApp Business number
4. Check that the transaction appears in your account

## Troubleshooting

- Check the `whatsapp_api.log` and `whatsapp_requests.log` files for error messages
- Ensure your phone number is correctly registered and in international format
- Verify that all environment variables are correctly set
- Make sure your webhook is properly configured in the Meta for Developers dashboard