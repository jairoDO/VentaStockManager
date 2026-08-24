# Imagen para correr el bridge wa-bot.
#
# Stack: Node.js 20 + @whiskeysockets/baileys.
#
# Baileys conecta directo al protocolo de WhatsApp por WebSocket +
# Signal Protocol, sin Puppeteer ni Chromium. Esto resuelve dos
# problemas que teníamos con la implementación anterior basada en
# @open-wa/wa-automate:
#
#   1. Apple Silicon: no necesitamos rosetta porque no corremos
#      Chromium x86_64 (ni Chromium en general).
#   2. Maintenance: open-wa quedó incompatible con el WA Web actual y
#      ya no se mantiene. Baileys está activo (1.3M downloads/mes).
#
# Resultado: imagen mucho más liviana (~200MB → ~80MB), arranque
# rápido (~5s vs ~30s del browser headless), sin dependencias gráficas.
FROM node:20-bookworm-slim

# Baileys solo necesita Node y libs estándar. Las únicas deps de
# sistema son las que algunos sub-paquetes (sharp para procesamiento
# de imágenes en `sendMessage`, etc.) podrían querer. Las dejamos
# mínimas y agregamos solo si se detecta una falla.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiamos solo el package.json primero para aprovechar la cache de
# layers cuando el code cambia pero las deps no.
COPY package.json ./
RUN npm install --omit=dev

COPY index.js safe-signal-logging.js safe-signal-logging.test.js ./

# Volume para persistir credenciales de Baileys (`useMultiFileAuthState`).
# Si se borra, hay que re-escanear el QR. Sobrevive a restarts del
# container con la misma sesión.
VOLUME ["/sessions"]

ENV WA_BOT_PORT=3000
ENV WA_BOT_SESSION_DIR=/sessions
EXPOSE 3000

CMD ["node", "index.js"]
