# wa-bot — bridge open-wa para VentaStockManager

Service Node.js que corre [`@open-wa/wa-automate`](https://www.open-wa.org/)
y expone una API HTTP simple para que Django pueda mandar mensajes de
WhatsApp sin lidiar con Puppeteer.

## Primera vez: escanear el QR

```bash
docker compose up -d wa-bot
docker compose logs -f wa-bot
```

El log va a mostrar:

1. `[wa-bot] Iniciando open-wa…`
2. Un **QR en ASCII** dibujado directamente en la terminal.
3. Si preferís un QR "de verdad" (más fácil de escanear con el celu),
   abrí `http://localhost:3000/qr` mientras el bot espera.

Escaneá con la app de WhatsApp (Menú → Dispositivos vinculados →
Vincular dispositivo). La sesión queda guardada en el volume
`wa-bot-sessions` y sobrevive restarts/deploys.

Cuando aparezca `[wa-bot] Cliente listo. WhatsApp conectado.`, está OK.

## Endpoints

| Método | Path           | Body                                        | Devuelve              |
|--------|----------------|---------------------------------------------|-----------------------|
| GET    | `/status`      | —                                           | `{ready, state, me}`  |
| GET    | `/qr`          | —                                           | PNG del QR o 204      |
| POST   | `/send-text`   | `{phone, message}`                          | `{ok, id}` o `{ok:false, error}` |
| POST   | `/send-media`  | `{phone, message?, base64, mime, filename?}`| `{ok, id}` o `{ok:false, error}` |

Formato de `phone`: solo dígitos con código de país, sin `+`.
Ejemplo: `5491155551234`.

## Autenticación

Variable de entorno **`WA_BOT_TOKEN`**:

- **Vacía o no seteada** → modo dev, sin auth. El bot loguea un
  warning bien visible al arrancar.
- **Con valor** → todos los endpoints exigen el header
  `X-Bot-Token: <valor>`. Para `/qr` también acepta `?token=<valor>`
  como query param (más cómodo para escanear desde el browser).

En docker-compose local se deja vacío. En producción siempre debe
estar seteado (generar uno aleatorio: `openssl rand -hex 32`).

## Cuándo se cae la sesión

- Si Osvaldo abre WhatsApp en el celu y va a Dispositivos vinculados →
  Cerrar sesión, se cae.
- Si WhatsApp decide vencer la sesión (cada 14 días sin actividad).
- Si el volume `wa-bot-sessions` se borra.

En todos los casos, basta con `docker compose logs wa-bot`, ver el
nuevo QR y re-escanear.

## Riesgos a tener presente

- **No es API oficial.** WhatsApp puede banear el número si detecta
  patrones automatizados. Por eso el bridge respeta un delay mínimo
  entre mensajes (configurado del lado de Django).
- **Conviene usar un número dedicado** (chip aparte) para evitar
  perder el WhatsApp principal del negocio si el ban llega.
