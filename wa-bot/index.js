/**
 * Bridge HTTP entre el Django de VentaStockManager y WhatsApp Web.
 *
 * Por qué este service vive aparte:
 *   - @open-wa/wa-automate es Node.js + Puppeteer. Meterlo en Django
 *     significaba child_process, infierno de zombies y la sesión de
 *     WhatsApp atada al ciclo de vida de gunicorn (cada reload del
 *     server pierde la sesión y hay que re-escanear QR).
 *   - Manteniéndolo en un container suyo, la sesión sobrevive deploys
 *     y restarts del web; el escaneo del QR es UNA SOLA VEZ.
 *
 * Endpoints expuestos en el puerto WA_BOT_PORT (default 3000):
 *   GET  /status          → {ready, session, info}
 *   GET  /qr              → PNG del QR (si todavía no hay sesión)
 *   POST /send-text       → {phone, message}                → {ok, id}
 *   POST /send-media      → {phone, message, base64, mime}  → {ok, id}
 *
 * Autenticación:
 *   - Si `WA_BOT_TOKEN` está seteado, TODOS los endpoints exigen el
 *     header `X-Bot-Token: <valor>`. Excepción: `/qr` también acepta
 *     `?token=<valor>` como query param, porque escanear el QR es más
 *     cómodo abriendo la URL desde el browser que armando un request.
 *   - Si la var está vacía (modo dev local), no se autentica nada y
 *     se loguea un warning bien visible al arrancar.
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const { create, decryptMedia } = require('@open-wa/wa-automate');

const PORT = process.env.WA_BOT_PORT || 3000;
const SESSION_DIR = process.env.WA_BOT_SESSION_DIR || '/sessions';
const SESSION_ID = process.env.WA_BOT_SESSION_ID || 'venta-stock';
const TOKEN = process.env.WA_BOT_TOKEN || '';

if (!TOKEN) {
  console.warn(
    '[wa-bot] ⚠ WA_BOT_TOKEN no seteado — modo DEV sin autenticación. ' +
    'NO usar así en producción.',
  );
} else {
  console.log('[wa-bot] Autenticación por token habilitada.');
}

const app = express();
// Body grande para adjuntos en base64 (PDFs/imágenes hasta ~16MB
// que es lo que WhatsApp permite).
app.use(express.json({ limit: '20mb' }));

/**
 * Middleware de autenticación.
 *
 * Si TOKEN está vacío, no exige nada (modo dev).
 * Si TOKEN está seteado, valida `X-Bot-Token` header. Para `/qr`
 * además acepta `?token=` query param (es más cómodo escanear el
 * QR abriendo la URL en el browser que armando un curl con headers).
 */
function authMiddleware(req, res, next) {
  if (!TOKEN) {
    return next();
  }
  const headerToken = req.get('X-Bot-Token');
  const queryToken = req.query && req.query.token;
  const provided = headerToken || (req.path === '/qr' ? queryToken : null);
  if (provided === TOKEN) {
    return next();
  }
  return res.status(401).json({ ok: false, error: 'unauthorized' });
}

app.use(authMiddleware);

let waClient = null;
let lastQrBase64 = null; // guardamos el QR en memoria para servirlo por HTTP

/**
 * Normaliza un número a chatId de WhatsApp.
 * Acepta: '5491155551234' → '5491155551234@c.us'
 *         '+5491155551234' → '5491155551234@c.us'
 *         '5491155551234@c.us' → mismo
 */
function toChatId(phone) {
  if (!phone) return null;
  if (phone.endsWith('@c.us') || phone.endsWith('@g.us')) return phone;
  const digits = String(phone).replace(/\D/g, '');
  if (!digits) return null;
  return `${digits}@c.us`;
}

/**
 * Endpoint de health/status. Lo usa el wrapper Python para chequear
 * si vale la pena intentar enviar antes de armar el payload.
 */
app.get('/status', async (req, res) => {
  if (!waClient) {
    return res.json({ ready: false, reason: 'client_not_initialized' });
  }
  try {
    const state = await waClient.getConnectionState();
    const me = await waClient.getMe().catch(() => null);
    res.json({ ready: state === 'CONNECTED', state, me });
  } catch (err) {
    res.json({ ready: false, reason: 'state_error', error: String(err) });
  }
});

/**
 * Sirve el último QR generado como PNG. Si la sesión ya está activa,
 * devuelve 204 (No Content) — no hay QR que mostrar.
 */
app.get('/qr', (req, res) => {
  if (!lastQrBase64) {
    return res.status(204).end();
  }
  const data = lastQrBase64.replace(/^data:image\/png;base64,/, '');
  const buf = Buffer.from(data, 'base64');
  res.set('Content-Type', 'image/png');
  res.send(buf);
});

app.post('/send-text', async (req, res) => {
  const { phone, message } = req.body || {};
  if (!phone || !message) {
    return res.status(400).json({ ok: false, error: 'phone y message son requeridos' });
  }
  const chatId = toChatId(phone);
  if (!chatId) {
    return res.status(400).json({ ok: false, error: 'phone inválido' });
  }
  if (!waClient) {
    return res.status(503).json({ ok: false, error: 'wa-bot no inicializado todavía' });
  }
  try {
    const id = await waClient.sendText(chatId, message);
    res.json({ ok: true, id });
  } catch (err) {
    res.status(500).json({ ok: false, error: String(err) });
  }
});

app.post('/send-media', async (req, res) => {
  const { phone, message, base64, mime, filename } = req.body || {};
  if (!phone || !base64) {
    return res.status(400).json({ ok: false, error: 'phone y base64 son requeridos' });
  }
  const chatId = toChatId(phone);
  if (!chatId) {
    return res.status(400).json({ ok: false, error: 'phone inválido' });
  }
  if (!waClient) {
    return res.status(503).json({ ok: false, error: 'wa-bot no inicializado todavía' });
  }
  try {
    // open-wa quiere un data URI completo (data:image/png;base64,...).
    // Si el caller mandó solo el base64 crudo, lo armamos acá.
    const dataUri = base64.startsWith('data:')
      ? base64
      : `data:${mime || 'application/octet-stream'};base64,${base64}`;
    const id = await waClient.sendFile(
      chatId,
      dataUri,
      filename || 'adjunto',
      message || '',
    );
    res.json({ ok: true, id });
  } catch (err) {
    res.status(500).json({ ok: false, error: String(err) });
  }
});

// --- Bootstrap ---
//
// `create()` lanza Chromium headless, restaura la sesión del volume
// si existe, y emite eventos a medida que cambia el estado.
//
// `qrCallback`: cada vez que open-wa genera un QR (al levantar sin
// sesión previa), lo guardamos en memoria + lo logueamos como ASCII
// en stdout. El operador puede:
//   - Ver el QR ASCII en `docker compose logs wa-bot`, o
//   - Abrir http://<host>:3000/qr en un browser y escanear desde ahí.
console.log('[wa-bot] Iniciando open-wa…');
console.log('[wa-bot] Sesión:', SESSION_ID, 'en', SESSION_DIR);

if (!fs.existsSync(SESSION_DIR)) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
}

create({
  sessionId: SESSION_ID,
  sessionDataPath: SESSION_DIR,
  multiDevice: true,
  headless: true,
  // ASCII QR en logs (cómodo cuando no podés abrir un browser).
  qrLogSkip: false,
  // Sin licencia: ciertas features avanzadas no andan, pero sendText
  // y sendFile sí.
  authTimeout: 0,
  killProcessOnBrowserClose: false,
  cacheEnabled: false,
  qrCallback: (qr) => {
    lastQrBase64 = qr;
    console.log('[wa-bot] QR generado. Abrí http://<host>:3000/qr para escanear, o mirá el ASCII de arriba.');
  },
})
  .then((client) => {
    waClient = client;
    lastQrBase64 = null;
    console.log('[wa-bot] Cliente listo. WhatsApp conectado.');
    client.onStateChanged((state) => {
      console.log('[wa-bot] State changed →', state);
      // Si la sesión se cae (logout desde el celular), open-wa se
      // entera y cambia el state. Logueamos para que el operador
      // sepa por qué el bot dejó de andar.
    });
  })
  .catch((err) => {
    console.error('[wa-bot] Error al inicializar open-wa:', err);
  });

app.listen(PORT, () => {
  console.log(`[wa-bot] HTTP escuchando en :${PORT}`);
});
