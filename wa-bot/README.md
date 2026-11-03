# wa-bot

Private Node.js bridge between VentaStockManager and WhatsApp. It uses [`@whiskeysockets/baileys`](https://github.com/WhiskeySockets/Baileys) and exposes a small HTTP API consumed by the Django application.

The service is deployed separately from Django and stores its WhatsApp authentication state on a persistent volume. In production it must remain private and require a shared authentication token.

## Run locally

```bash
docker compose up -d wa-bot
docker compose logs -f wa-bot
```

On first start, scan the QR code with WhatsApp under **Linked devices**. The session is stored in the `wa_bot_sessions` Docker volume and survives container restarts.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/status` | Connection and readiness status |
| `GET` | `/qr` | QR code used to establish a session |
| `POST` | `/send-text` | Send a text message |
| `POST` | `/send-media` | Send a media attachment |

Phone numbers use international digits without the `+` prefix.

## Authentication

Set `WA_BOT_TOKEN` and send the same value from Django as `WHATSAPP_API_TOKEN`.

- An empty token is allowed only for local development.
- Production endpoints must require the `X-Bot-Token` header.
- Store the token in Render environment variables; never commit it.

## Tests

```bash
npm install
npm test
```

The current test suite verifies that sensitive Signal protocol session data is redacted from logs.

## Operational risks

- This integration does not use the official WhatsApp Business API.
- Automated messaging can lead to account restrictions; use a dedicated business number and conservative sending limits.
- Deleting the persistent session volume requires pairing the account again.
- Do not expose this service directly to the public internet.
