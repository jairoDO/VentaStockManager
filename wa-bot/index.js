/**
 * Bridge HTTP entre el Django de VentaStockManager y WhatsApp.
 *
 * Implementado con @whiskeysockets/baileys (no usa Puppeteer/Chromium).
 * Migrado desde @open-wa/wa-automate v4.76 que quedó incompatible con
 * el WhatsApp Web actual y dejó de mantenerse. Baileys conecta directo
 * al protocolo de WhatsApp via WebSocket + Signal Protocol — mucho más
 * liviano y robusto, no rompe cada vez que WhatsApp Web cambia algo
 * en el frontend.
 *
 * IMPORTANTE: la API HTTP es la MISMA que la versión anterior, así
 * el cliente Python en `wa_campania/wa_client.py` y el panel admin
 * en `/wa-campania/conexion/` no necesitan ningún cambio.
 *
 * Endpoints expuestos en el puerto WA_BOT_PORT (default 3000):
 *   GET  /status          → {ready, state, me}
 *   GET  /qr              → PNG del QR (si todavía no hay sesión)
 *   POST /send-text       → {phone, message}                → {ok, id}
 *   POST /send-media      → {phone, message, base64, mime}  → {ok, id}
 *   POST /logout          → cierra sesión y borra credenciales
 *   POST /restart         → reinicia el proceso (mantiene sesión)
 *
 * Estados expuestos en /status (compatible con panel admin):
 *   - 'starting'              → arrancando, todavía no hay socket
 *   - 'UNPAIRED'              → hay QR para escanear
 *   - 'PAIRING'               → escaneo del QR fue confirmado, completando
 *   - 'CONNECTED'             → sesión activa, listo para enviar
 *   - 'connection_error'      → desconectado por error
 *   - 'logged_out'            → cuenta fue desvinculada desde el celular
 *
 * Autenticación: si `WA_BOT_TOKEN` está seteado, todos los endpoints
 * exigen `X-Bot-Token: <valor>` (excepto `/qr` que también acepta
 * `?token=` para que se pueda escanear el QR desde un browser).
 */

const { default: makeWASocket,
        useMultiFileAuthState,
        DisconnectReason,
        fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const express = require('express');
const fs = require('fs');
const path = require('path');
const pino = require('pino');
const QRCode = require('qrcode');
const { installSafeSignalLogging } = require('./safe-signal-logging');

// libsignal imprime el objeto SessionEntry completo al cerrar una sesión,
// incluyendo claves privadas y ratchets. Filtramos únicamente ese mensaje;
// el resto de console.info sigue funcionando normalmente.
installSafeSignalLogging(console);

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

// Logger de Baileys: silencioso por default para no ahogar la consola
// con paquetes de protocolo. Subir a 'info' o 'debug' para troubleshoot.
const logger = pino({ level: process.env.BAILEYS_LOG_LEVEL || 'fatal' });

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
  if (!TOKEN) return next();
  const headerToken = req.get('X-Bot-Token');
  const queryToken = req.query && req.query.token;
  const provided = headerToken || (req.path === '/qr' ? queryToken : null);
  if (provided === TOKEN) return next();
  return res.status(401).json({ ok: false, error: 'unauthorized' });
}

app.use(authMiddleware);

// ---- Estado global ----
//
// Baileys conecta y desconecta solo según los eventos del socket. No
// hay una "instancia única" persistente como en open-wa — guardamos
// referencias para que los endpoints accedan al socket actual.
let sock = null;          // instancia WAS del socket activo
let lastQR = null;        // string del QR vigente (raw text del QR)
let lastQRPng = null;     // bytes del PNG renderizado (cacheado)
let me = null;            // info de la cuenta conectada (id, name)
let connectionState = 'starting';  // ver lista arriba
let lastDisconnectMsg = '';        // motivo del último disconnect (debug)
let reconnectTimer = null;          // evita reconexiones superpuestas

/**
 * Convierte un número telefónico arbitrario a un JID de WhatsApp.
 *
 * Asumimos que TODOS los clientes son argentinos. Si el número viene
 * en formato corto (10 dígitos, móvil AR sin internacional), le
 * agregamos `549` automáticamente. Esto blinda contra el operador que
 * carga un número como "3513452496" sin saber del código país.
 *
 * Acepta:
 *   '5491155551234'                     → '5491155551234@s.whatsapp.net'
 *   '+5491155551234'                    → '5491155551234@s.whatsapp.net'
 *   '3513452496'  (10 dígitos AR)       → '5493513452496@s.whatsapp.net'  ← auto-prefijo
 *   '113513452496' (11 dígitos sin 54)  → '54113513452496@s.whatsapp.net'
 *   '5491155551234@s.whatsapp.net'      → mismo
 *   '5491155551234@c.us' (formato legacy) → '5491155551234@s.whatsapp.net'
 *
 * Aceptamos `@c.us` para que el código que migró desde open-wa siga
 * funcionando sin tocar — internamente Baileys usa `@s.whatsapp.net`.
 */
function toJID(phone) {
  if (!phone) return null;
  if (typeof phone !== 'string') phone = String(phone);
  if (phone.includes('@g.us')) return phone;  // grupo, dejar como viene
  if (phone.includes('@s.whatsapp.net')) return phone;
  if (phone.includes('@c.us')) {
    return phone.replace('@c.us', '@s.whatsapp.net');
  }
  let digits = phone.replace(/\D/g, '');
  if (!digits) return null;

  // Auto-prefijo AR — pero SOLO si el número parece argentino "corto".
  // Si el número ya tiene 11+ dígitos y NO empieza con 54, asumimos que
  // es internacional (ej. +61 Australia, +1 USA, +44 UK) y NO le tocamos
  // el prefijo. Antes anteponíamos '54' a TODO 11+ sin 54 → rompía
  // mensajes a clientes/contactos no-AR (ej. 61451347124 → 5461451347124).
  //
  // Reglas iguales al normalizador Python en cliente/phone_utils.py
  // (mantener en sync).
  if (digits.startsWith('0')) digits = digits.replace(/^0+/, '');  // 0 prefijo nacional AR
  if (digits.length === 10 && !digits.startsWith('54')) {
    digits = '549' + digits;  // móvil AR sin internacional → completar
  }
  // 11+ dígitos sin 54: dejamos como está (asumimos internacional).
  // 11+ dígitos con 54: ya está formateado correctamente.

  return `${digits}@s.whatsapp.net`;
}

/**
 * True si el JID destino es el MISMO número con el que está vinculado
 * el bot. WhatsApp no entrega notificaciones de mensajes "a vos
 * mismo" — el mensaje va a "Mensajes contigo" pero no aparece como
 * recibido. Esto causa el caso engañoso "el bot dice enviado pero
 * yo no veo nada en el celular".
 */
function isSelfJID(jid) {
  if (!jid || !me || !me.id) return false;
  // me.id puede venir con sufijo `:NN` (ej. '5493513452496:10@s.whatsapp.net').
  // Comparamos solo los dígitos antes del primer @ o `:`.
  const meDigits = String(me.id).split('@')[0].split(':')[0];
  const jidDigits = String(jid).split('@')[0].split(':')[0];
  return meDigits && jidDigits && meDigits === jidDigits;
}

// -------------------------------------------------------------------
// Bootstrap del socket Baileys
// -------------------------------------------------------------------
async function startSock() {
  // Aseguramos el directorio de sesiones (cuando el volume está vacío).
  if (!fs.existsSync(SESSION_DIR)) {
    fs.mkdirSync(SESSION_DIR, { recursive: true });
  }
  const sessionPath = path.join(SESSION_DIR, SESSION_ID);
  if (!fs.existsSync(sessionPath)) {
    fs.mkdirSync(sessionPath, { recursive: true });
  }

  // `useMultiFileAuthState`: persiste credenciales (keys de Signal,
  // pre-keys, app state) en archivos del volume para sobrevivir
  // restarts sin re-escanear QR.
  const { state, saveCreds } = await useMultiFileAuthState(sessionPath);

  // Pinear la versión de WhatsApp Web que Baileys usa al hacer
  // handshake. fetchLatestBaileysVersion consulta el endpoint público
  // del proyecto y devuelve la versión que matchea con el WA actual.
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`[wa-bot] Baileys version=${version.join('.')} (isLatest=${isLatest})`);

  const currentSock = makeWASocket({
    version,
    auth: state,
    logger,
    // No imprimir QR en terminal — lo servimos vía /qr a la UI admin.
    // (Baileys imprime ASCII por default si lo dejás en true.)
    printQRInTerminal: false,
    // Browser fingerprint para que WhatsApp nos identifique
    // razonablemente. El array es [name, browser, version].
    browser: ['VentaStockManager', 'Chrome', '120.0.0'],
    // ── Optimizaciones de bandwidth ─────────────────────────────────
    // Nuestro caso de uso es: enviar mensajes (listas de precios,
    // recordatorios, respuestas automáticas a "lista"/"saldo"). NO
    // leemos historial, NO procesamos media. Las flags de abajo
    // recortan ~80% del consumo de Baileys, que era el principal
    // chupador de banda en Render (5GB/semana al primer plan Hobby).
    //
    // syncFullHistory: false — no descargar histórico completo al
    //   loguearse. Aun así Baileys baja un "delta" pequeño por
    //   default. Lo recortamos abajo.
    syncFullHistory: false,
    // shouldSyncHistoryMessage: rechazar TODOS los notify de
    //   historial. Cuando Baileys recibe un push de "mensaje pasado"
    //   (typicamente al re-conectar), lo descarta sin descargar
    //   media adjunta. Para auto-respuesta solo necesitamos NUEVOS.
    shouldSyncHistoryMessage: () => false,
    // markOnlineOnConnect: false — no mandar "online" al servidor.
    //   Reduce el handshake inicial + no notifica a contactos que
    //   el bot está activo. (Side effect deseado: los clientes no
    //   ven el check azul/online del número del bot.)
    markOnlineOnConnect: false,
    // getMessage: cuando WhatsApp pide retry de un mensaje viejo
    //   (típicamente cuando el otro lado no recibió un decrypt),
    //   normalmente Baileys lo busca en su store. Como no guardamos
    //   store, devolvemos undefined → Baileys deja de intentar
    //   reenviar mensajes históricos.
    getMessage: async () => undefined,
  });
  sock = currentSock;

  // Persistir credenciales cuando cambien (después de cada handshake).
  currentSock.ev.on('creds.update', saveCreds);

  // Auto-responder: cuando entra un mensaje, lo forwardeamos a Django
  // (endpoint /wa-campania/api/incoming/) que decide qué hacer. Si
  // Django responde {action: 'reply_text'} o {action: 'reply_media'},
  // lo ejecutamos. Si responde {action: 'ignore'}, no hacemos nada.
  //
  // Filtramos antes de llamar al backend para no gastar requests:
  //   - Ignorar mensajes propios (fromMe).
  //   - Ignorar grupos (@g.us) y status broadcast (@broadcast).
  //   - Ignorar mensajes no-texto (audios, fotos, etc. — el operador
  //     los responde a mano).
  currentSock.ev.on('messages.upsert', async ({ messages, type }) => {
    // type='notify' = mensaje nuevo. 'append' = relleno de historial.
    // Solo procesamos los notify (mensajes que llegan en vivo).
    if (type !== 'notify') return;

    for (const msg of messages || []) {
      try {
        await handleIncomingMessage(msg);
      } catch (err) {
        console.error('[wa-bot] handleIncomingMessage falló:', err);
        // No re-throw: un mensaje que rompe no debe matar al handler.
      }
    }
  });

  // Eventos de conexión: QR, conexión exitosa, desconexión.
  currentSock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      lastQR = qr;
      try {
        lastQRPng = await QRCode.toBuffer(qr, {
          type: 'png',
          width: 300,
          margin: 1,
        });
      } catch (err) {
        console.error('[wa-bot] Error renderizando QR:', err);
      }
      connectionState = 'UNPAIRED';
      console.log('[wa-bot] QR generado. Escaneá en el panel admin (/wa-campania/conexion/).');
    }

    if (connection === 'connecting') {
      // No tocamos connectionState acá si ya estamos en 'UNPAIRED' o
      // 'CONNECTED' — 'connecting' es un estado transitorio que
      // aparece muchas veces durante el ciclo de vida y no es útil
      // mostrarlo en la UI.
      if (connectionState === 'starting') {
        connectionState = 'PAIRING';
      }
    }

    if (connection === 'open') {
      if (sock !== currentSock) return;
      me = currentSock.user || null;
      connectionState = 'CONNECTED';
      lastQR = null;
      lastQRPng = null;
      lastDisconnectMsg = '';
      console.log(
        '[wa-bot] Cliente listo. Conectado como',
        me?.id || '(sin id)', '·', me?.name || '(sin nombre)',
      );
    }

    if (connection === 'close') {
      // Un socket viejo puede emitir su cierre después de que ya arrancó
      // otro. Ignorarlo evita borrar la sesión recién creada.
      if (sock !== currentSock) return;

      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const reason = lastDisconnect?.error?.message || 'sin detalle';
      lastDisconnectMsg = `${statusCode || '?'}: ${reason}`;

      const isLoggedOut = statusCode === DisconnectReason.loggedOut;
      if (isLoggedOut) {
        // El usuario desvinculó la sesión desde el celular. Borrar
        // archivos y crear un socket fresco que pida QR, sin tumbar el
        // proceso HTTP ni hacer que Render lo marque como caída.
        connectionState = 'logged_out';
        me = null;
        lastQR = null;
        lastQRPng = null;
        console.warn(
          '[wa-bot] Sesión desvinculada desde el celular. '
          + 'Se limpiarán las credenciales y se generará un QR nuevo.',
        );
        currentSock.ev.removeAllListeners('creds.update');
        try {
          fs.rmSync(sessionPath, { recursive: true, force: true });
        } catch (e) { /* ignore */ }
        sock = null;
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connectionState = 'starting';
          startSock().catch((err) => {
            connectionState = 'connection_error';
            console.error('[wa-bot] No se pudo generar una sesión nueva:', err.message || err);
          });
        }, 1000);
        return;
      }

      // Cualquier otra desconexión (network, timeout, restart) →
      // intentamos reconectar automáticamente. Baileys NO reconecta
      // solo — lo hacemos acá.
      connectionState = 'connection_error';
      console.log('[wa-bot] Desconectado.', lastDisconnectMsg, '— reconectando en 3s…');
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        startSock().catch((err) => {
          console.error('[wa-bot] Reconexión falló:', err);
        });
      }, 3000);
    }
  });
}

// -------------------------------------------------------------------
// Endpoints HTTP
// -------------------------------------------------------------------

/**
 * Status: lo que polea el panel admin para mostrar el estado.
 */
app.get('/status', async (req, res) => {
  // Si no hay socket todavía (process arrancando), devolvemos
  // 'starting' para que la UI muestre "Iniciando bot…".
  if (!sock) {
    return res.json({
      ready: false,
      state: connectionState === 'starting' ? 'client_not_initialized' : connectionState,
      reason: 'client_not_initialized',
    });
  }
  res.json({
    ready: connectionState === 'CONNECTED',
    state: connectionState,
    me: me ? {
      // Format compatible con lo que devolvía open-wa para que el
      // template del panel admin no necesite cambios.
      id: { user: (me.id || '').split('@')[0], _serialized: me.id },
      pushname: me.name || '',
      name: me.name || '',
    } : null,
    lastDisconnectMsg: lastDisconnectMsg || undefined,
  });
});

/**
 * Sirve el último QR como PNG. 204 si no hay QR (ya conectado o aún
 * no se generó).
 */
app.get('/qr', (req, res) => {
  if (!lastQRPng) {
    return res.status(204).end();
  }
  res.set('Content-Type', 'image/png');
  res.set('Cache-Control', 'no-store, no-cache, must-revalidate');
  res.send(lastQRPng);
});

/**
 * Verifica si un número está registrado en WhatsApp ANTES de mandarle.
 *
 * GET /exists?phone=5491155551234
 *   → 200 {exists: true, jid: "5491155551234@s.whatsapp.net"}
 *   → 200 {exists: false}
 *
 * Por qué: si el número NO está en WhatsApp (ej. cargado con formato
 * inválido), `sock.sendMessage` igual responde OK pero el mensaje
 * nunca llega. Es un fallo silencioso. Con esta verificación, la
 * task de difusión puede marcar "fallido: número no existe en WA"
 * en vez de "enviado falso".
 */
app.get('/exists', async (req, res) => {
  const phone = req.query && req.query.phone;
  if (!phone) {
    return res.status(400).json({ ok: false, error: 'phone es requerido' });
  }
  const jid = toJID(phone);
  if (!jid) {
    return res.status(400).json({ ok: false, error: 'phone inválido' });
  }
  if (!sock || connectionState !== 'CONNECTED') {
    return res.status(503).json({ ok: false, error: 'wa-bot no conectado' });
  }
  // Self-check: si es el mismo número del bot, devolver exists:false
  // con motivo claro. Mandarse a sí mismo en WhatsApp no genera
  // notificación normal — el mensaje va a "Mensajes contigo" en
  // silencio y al operador le parece que no llegó.
  if (isSelfJID(jid)) {
    return res.json({
      ok: true,
      exists: false,
      reason: 'self',
      message: (
        'Ese número es el mismo con el que está vinculado el bot. '
        + 'WhatsApp no entrega notificaciones cuando te mandás a vos mismo. '
        + 'Probá con otro número.'
      ),
    });
  }
  try {
    // `onWhatsApp` devuelve un array. Si el JID está registrado,
    // viene con `{exists: true, jid: '...'}`. Si no, viene vacío.
    const results = await sock.onWhatsApp(jid);
    const found = Array.isArray(results) && results.length > 0 && results[0].exists;
    if (found) {
      // Baileys puede devolver un JID "limpio" (sin el sufijo `:NN` que
      // a veces aparece). Usamos el que devolvió onWhatsApp para que
      // sendMessage no se confunda.
      return res.json({ ok: true, exists: true, jid: results[0].jid });
    }
    return res.json({ ok: true, exists: false });
  } catch (err) {
    console.error('[wa-bot] /exists falló:', err);
    res.status(500).json({ ok: false, error: String(err.message || err) });
  }
});

/**
 * Envío de texto.
 */
app.post('/send-text', async (req, res) => {
  const { phone, message } = req.body || {};
  if (!phone || !message) {
    return res.status(400).json({ ok: false, error: 'phone y message son requeridos' });
  }
  const jid = toJID(phone);
  if (!jid) {
    return res.status(400).json({ ok: false, error: 'phone inválido' });
  }
  if (isSelfJID(jid)) {
    return res.status(400).json({
      ok: false,
      error: (
        'No podés mandarte mensajes a vos mismo: el bot está vinculado '
        + 'con este número. WhatsApp aceptaría el envío pero no lo '
        + 'mostraría como recibido (va a "Mensajes contigo" en silencio).'
      ),
    });
  }
  if (!sock || connectionState !== 'CONNECTED') {
    return res.status(503).json({ ok: false, error: 'wa-bot no conectado' });
  }
  try {
    const sent = await sock.sendMessage(jid, { text: message });
    res.json({ ok: true, id: sent?.key?.id || '' });
  } catch (err) {
    console.error('[wa-bot] send-text falló:', err);
    res.status(500).json({ ok: false, error: String(err.message || err) });
  }
});

/**
 * Envío de archivo (imagen o documento). El caller manda los bytes
 * en base64; nosotros decidimos si va como image o document según
 * el mime type.
 */
app.post('/send-media', async (req, res) => {
  const { phone, message, base64, mime, filename } = req.body || {};
  if (!phone || !base64) {
    return res.status(400).json({ ok: false, error: 'phone y base64 son requeridos' });
  }
  const jid = toJID(phone);
  if (!jid) {
    return res.status(400).json({ ok: false, error: 'phone inválido' });
  }
  if (isSelfJID(jid)) {
    return res.status(400).json({
      ok: false,
      error: (
        'No podés mandarte adjuntos a vos mismo: el bot está vinculado '
        + 'con este número. WhatsApp no notifica este caso.'
      ),
    });
  }
  if (!sock || connectionState !== 'CONNECTED') {
    return res.status(503).json({ ok: false, error: 'wa-bot no conectado' });
  }
  try {
    // El caller puede mandar el base64 como data URI (`data:...;base64,XXX`)
    // o como base64 puro. Limpiamos el prefijo si está.
    const cleanB64 = base64.replace(/^data:[^;]+;base64,/, '');
    const buf = Buffer.from(cleanB64, 'base64');

    const isImage = (mime || '').startsWith('image/');
    let payload;
    if (isImage) {
      // Imagen → mostrar inline en el chat con caption.
      payload = {
        image: buf,
        caption: message || '',
      };
    } else {
      // PDFs y cualquier otra cosa → como documento adjunto.
      payload = {
        document: buf,
        mimetype: mime || 'application/octet-stream',
        fileName: filename || 'adjunto',
        caption: message || '',
      };
    }
    const sent = await sock.sendMessage(jid, payload);
    res.json({ ok: true, id: sent?.key?.id || '' });
  } catch (err) {
    console.error('[wa-bot] send-media falló:', err);
    res.status(500).json({ ok: false, error: String(err.message || err) });
  }
});

/**
 * Logout: desconecta la sesión y borra credenciales. La próxima
 * conexión va a pedir QR nuevo.
 */
app.post('/logout', async (req, res) => {
  if (!sock) {
    return res.json({ ok: true, noop: true, message: 'wa-bot no inicializado' });
  }
  try {
    await sock.logout();
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: String(err.message || err) });
  } finally {
    // Borrar credenciales para forzar QR fresco en el próximo arranque.
    try {
      const sessionPath = path.join(SESSION_DIR, SESSION_ID);
      fs.rmSync(sessionPath, { recursive: true, force: true });
    } catch (e) { /* ignore */ }
    sock = null;
    me = null;
    lastQR = null;
    lastQRPng = null;
    connectionState = 'logged_out';
    // process.exit(0) para que docker-compose reinicie limpio
    // (con restart: unless-stopped).
    setTimeout(() => {
      console.log('[wa-bot] Logout → process.exit para arranque limpio.');
      process.exit(0);
    }, 500);
  }
});

/**
 * Reinicia el proceso SIN borrar credenciales. Útil cuando quedó en
 * estado raro pero la sesión sigue siendo válida.
 */
app.post('/restart', (req, res) => {
  res.json({ ok: true, message: 'Reiniciando wa-bot…' });
  setTimeout(() => {
    console.log('[wa-bot] /restart solicitado → process.exit(0)');
    process.exit(0);
  }, 200);
});

// -------------------------------------------------------------------
// Auto-responder de mensajes entrantes
// -------------------------------------------------------------------

// URL del backend Django. En docker-compose ambos servicios están en
// la misma red interna — `web` es el hostname. Fuera de docker
// (testing local) usamos localhost.
const DJANGO_URL = process.env.DJANGO_URL || 'http://web:8000';

/**
 * Devuelve el texto plano de un mensaje WhatsApp si es texto.
 * Mensajes con foto/audio/video/etc devuelven null (los ignoramos,
 * el operador los responde a mano en WhatsApp Web normal).
 */
function extractText(msg) {
  const m = msg.message;
  if (!m) return null;
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage && m.extendedTextMessage.text) {
    return m.extendedTextMessage.text;
  }
  // Mensajes con caption (foto + texto, etc.) — devolvemos el caption
  // por si el cliente puso "lista" en el caption.
  if (m.imageMessage && m.imageMessage.caption) return m.imageMessage.caption;
  if (m.videoMessage && m.videoMessage.caption) return m.videoMessage.caption;
  if (m.documentMessage && m.documentMessage.caption) return m.documentMessage.caption;
  return null;
}

/**
 * Maneja UN mensaje entrante. Filtra los que no son interesantes y
 * para el resto consulta a Django si hay que responder.
 */
async function handleIncomingMessage(msg) {
  // Ignorar mensajes propios (los que MANDÓ el bot — vuelven en upsert
  // también como confirmación). Si los procesáramos como entrantes
  // se podría armar un loop infinito de auto-respuestas.
  if (!msg.key || msg.key.fromMe) return;

  const remoteJid = msg.key.remoteJid || '';

  // Ignorar grupos y broadcasts. Solo respondemos a chats 1:1.
  if (remoteJid.endsWith('@g.us')) return;
  if (remoteJid.endsWith('@broadcast')) return;
  if (remoteJid === 'status@broadcast') return;

  // Extraer texto. Si es solo media (foto/audio sin caption), ignorar.
  const text = extractText(msg);
  if (!text || !text.trim()) return;

  // Sacar el NÚMERO REAL del cliente. Acá había un bug grueso:
  //
  // WhatsApp puede mandar mensajes con `remoteJid` en formato `@lid`
  // (Linked Identity, ej. "130459681976522@lid") en vez del PN real
  // (ej. "5493512894229@s.whatsapp.net"). Esto pasa cuando el chat
  // está mapeado a una linked identity (común en cuentas business o
  // multi-device). El número del LID NO es el número telefónico — es
  // un ID interno de WhatsApp que NO existe en nuestra DB de clientes.
  //
  // Resultado del bug: el bot loggeaba "cliente_desconocido" para
  // mensajes reales de clientes que SÍ están cargados, porque
  // buscaba por "130459681976522" en lugar de "5493512894229".
  //
  // Fix: si el JID es @lid, preferir senderPn (1:1) o participantPn
  // (grupos, aunque acá ya están filtrados) que contiene el PN real.
  // Fallback al remoteJid solo si no hay PN (chats viejos sin LID).
  let realJid = remoteJid;
  if (remoteJid.endsWith('@lid')) {
    realJid = msg.key.senderPn || msg.key.participantPn || remoteJid;
    if (realJid === remoteJid) {
      console.log(`[wa-bot] LID sin PN resoluble: ${remoteJid} — ignorando.`);
      return;
    }
  }
  const fromDigits = realJid.split('@')[0].split(':')[0];

  // Llamar a Django para que decida qué hacer.
  let resultado;
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (TOKEN) headers['X-Bot-Token'] = TOKEN;
    const response = await fetch(`${DJANGO_URL}/wa-campania/api/incoming/`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        from: fromDigits,
        text: text,
        message_id: msg.key.id || '',
      }),
    });
    resultado = await response.json();
  } catch (err) {
    console.error('[wa-bot] No pude llamar a Django /incoming/:', err.message);
    return;
  }

  if (!resultado || resultado.action === 'ignore') {
    // Logueo opcional para debug. Quitar si genera mucho ruido.
    if (resultado && resultado.reason) {
      console.log(`[wa-bot] Ignoramos mensaje de ${fromDigits}: ${resultado.reason}`);
    }
    return;
  }

  // Marcar como leído antes de responder — buena práctica de UX
  // (el cliente ve "visto" cuando llega la respuesta).
  try {
    await sock.readMessages([msg.key]);
  } catch (e) { /* no crítico */ }

  // Ejecutar la respuesta.
  if (resultado.action === 'reply_text') {
    try {
      await sock.sendMessage(remoteJid, { text: resultado.text || '' });
      console.log(`[wa-bot] Auto-respondido (texto) a ${fromDigits}`);
    } catch (err) {
      console.error('[wa-bot] reply_text falló:', err.message);
    }
  } else if (resultado.action === 'reply_media') {
    const att = resultado.attachment || {};
    if (!att.base64) {
      console.warn('[wa-bot] reply_media sin attachment, salteo');
      return;
    }
    try {
      const buf = Buffer.from(att.base64, 'base64');
      const mime = att.mime || 'application/octet-stream';
      const filename = att.filename || 'adjunto';
      const payload = mime.startsWith('image/')
        ? { image: buf, caption: resultado.text || '' }
        : { document: buf, mimetype: mime, fileName: filename, caption: resultado.text || '' };
      await sock.sendMessage(remoteJid, payload);
      console.log(`[wa-bot] Auto-respondido (media) a ${fromDigits}: ${filename}`);
    } catch (err) {
      console.error('[wa-bot] reply_media falló:', err.message);
    }
  }
}

// -------------------------------------------------------------------
// Startup
// -------------------------------------------------------------------
console.log('[wa-bot] Iniciando Baileys…');
console.log('[wa-bot] Sesión:', SESSION_ID, 'en', SESSION_DIR);

startSock().catch((err) => {
  console.error('[wa-bot] Error al inicializar Baileys:', err);
});

app.listen(PORT, () => {
  console.log(`[wa-bot] HTTP escuchando en :${PORT}`);
});
