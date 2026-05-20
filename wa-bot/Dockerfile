# Imagen para correr el bridge wa-bot.
#
# Por qué `node:20-bookworm-slim` y no `alpine`: @open-wa/wa-automate
# arrastra Puppeteer + Chromium, y Chromium en Alpine requiere builds
# musl que históricamente no son confiables. Debian slim es 200MB más
# pesado pero anda sin sorpresas.
FROM node:20-bookworm-slim

# Dependencias del sistema para que Chromium headless arranque dentro
# del container. Esta lista sale de la documentación oficial de
# Puppeteer (https://pptr.dev/troubleshooting#chrome-headless-doesnt-launch).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiamos solo el package.json primero para aprovechar la cache de
# layers cuando el code change pero las deps no.
COPY package.json ./
RUN npm install --omit=dev

COPY index.js ./

# Volume para persistir la sesión de WhatsApp. Si se borra, hay que
# re-escanear el QR.
VOLUME ["/sessions"]

ENV WA_BOT_PORT=3000
ENV WA_BOT_SESSION_DIR=/sessions
EXPOSE 3000

CMD ["node", "index.js"]
