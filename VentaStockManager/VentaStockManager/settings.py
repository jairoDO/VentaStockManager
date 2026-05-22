"""
Django settings for VentaStockManager.

Toda la configuración sensible o que cambia entre entornos (local, staging,
production) se lee desde variables de entorno. Ver `.env.example` para la
lista completa con valores de referencia.

Variables clave:
    SECRET_KEY              Requerida en producción.
    DEBUG                   "True"/"1" para desarrollo. Default: False.
    ALLOWED_HOSTS           Lista separada por comas.
    CSRF_TRUSTED_ORIGINS    Lista separada por comas (con https://).
    DATABASE_URL            Postgres/MySQL/sqlite URL. Default: sqlite local.
    GOOGLE_CREDENTIALS_PATH Ruta al JSON del service account de Google.
    GOOGLE_SHEET_ID         ID de la planilla de artículos.
"""

import mimetypes
import os
from pathlib import Path

import dj_database_url
from environs import Env

env = Env()
env.read_env()  # Lee .env del directorio actual si existe

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = env.str(
    "SECRET_KEY",
    default="django-insecure-dev-only-key-DO-NOT-USE-IN-PRODUCTION",
)
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0"],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Cuando vivimos detrás de un proxy con TLS (Render, ngrok, etc.) Django
# necesita saber leer el header forwarded para reconocer la conexión como
# segura.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookies seguras solo cuando no estamos en dev.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# ---------------------------------------------------------------------------
# Apps + middleware
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    'material',
    # material.admin registra label='material_admin' (no 'admin'), por lo
    # tanto NO conflictua con django.contrib.admin. El comentario original
    # "avoid duplicate admin label" era incorrecto.
    'material.admin',
    # django.contrib.admin tiene que estar para que @admin.register en
    # django.contrib.auth.admin funcione (default_site lookup).
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'whitenoise.runserver_nostatic',
    'dal',
    'dal_select2',
    'django_extensions',
    'cliente.apps.ClienteConfig',
    'venta.apps.VentaConfig',
    'articulo.apps.ArticuloConfig',
    'vendedor.apps.VendedorConfig',
    'compra.apps.CompraConfig',
    'django_q',
    'factura_config.apps.FacturaConfigConfig',
    # Campañas de WhatsApp. La app habla con un service Node.js
    # aparte (`wa-bot` en docker-compose) que corre open-wa.
    'wa_campania.apps.WaCampaniaConfig',
    # Configuración operativa runtime (singleton). Centraliza
    # parámetros que el admin necesita poder cambiar sin tocar
    # variables de entorno (retención de ventas).
    'configuracion.apps.ConfiguracionConfig',
    # Audit log: registra create/update/delete sobre los modelos
    # de negocio (ver venta/apps.py, articulo/apps.py, etc.). Las
    # entradas se ven en /admin/auditlog/logentry/.
    'auditlog',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Captura el request.user para asociar cada cambio en el
    # AuditLog al usuario que lo hizo. Tiene que ir DESPUÉS del
    # AuthenticationMiddleware.
    'auditlog.middleware.AuditlogMiddleware',
]

ROOT_URLCONF = 'VentaStockManager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'compra', 'templates'),
            os.path.join(BASE_DIR, 'venta', 'templates'),
            os.path.join(BASE_DIR, 'cliente', 'templates'),
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# `dj_database_url` parsea DATABASE_URL en formato:
#   postgres://user:pass@host:port/dbname?sslmode=require
# Si la variable no existe, cae al sqlite local (útil para `manage.py runserver`).
DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    ),
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ---------------------------------------------------------------------------
# i18n / l10n
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'es'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True


# ---------------------------------------------------------------------------
# Static files (Whitenoise)
# ---------------------------------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'articulo', 'static'),
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]


# ---------------------------------------------------------------------------
# Misc Django
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
ADMIN_MEDIA_PREFIX = '/static/admin/'


# ---------------------------------------------------------------------------
# django-material admin
# ---------------------------------------------------------------------------
MATERIAL_ADMIN_SITE = {
    'SHOW_THEMES': True,
    'TRAY_REVERSE': True,
    'NAVBAR_REVERSE': True,
    'SHOW_COUNTS': True,
    # Refresh visual: acercar el admin a la paleta de las pantallas
    # Tailwind (venta nueva, extracto, panel-tareas). Estos colores
    # del MATERIAL_ADMIN_SITE se inyectan como CSS vars en el :root,
    # por lo que el header, botones primarios y links cambian sin
    # tocar templates.
    #   - bg principal: slate-700 (un gris azulado calmo, no el azul
    #     material clásico que era muy saturado)
    #   - hover: blue-600 de Tailwind, mismo del que se usan en los
    #     botones de la pantalla nueva
    'MAIN_BG_COLOR': '#334155',
    'MAIN_HOVER_COLOR': '#2563eb',
}


# ---------------------------------------------------------------------------
# django-q (async tasks via DB ORM)
# ---------------------------------------------------------------------------
Q_CLUSTER = {
    'name': 'DjangoQ',
    # En Render Starter (512MB) el qcluster comparte dyno con gunicorn
    # (vía honcho). Con 4 workers de qcluster + 2 de gunicorn + Django
    # cargado dos veces, el OOM-killer dispara fácil. 2 workers de
    # qcluster alcanza para nuestro throughput (envío de WhatsApp uno
    # por uno con rate limit, archivado de ventas 1x/día, sync delete
    # esporádico). Si en algún momento se cuella, subir a 3 y medir.
    'workers': 2,
    'recycle': 500,
    'timeout': 60,
    'queue_limit': 50,
    'bulk': 10,
    'orm': 'default',
}


# ---------------------------------------------------------------------------
# Google Sheets sync (articulos)
# ---------------------------------------------------------------------------
# El service-account JSON nunca se commitea. En Render se sube como Secret File
# y se monta en /etc/secrets/google-credentials.json. Localmente, apuntá a tu
# copia fuera del repo (ej. ~/credentials-backup/golosinas-insa-credentials.json).
GOOGLE_CREDENTIALS_PATH = env.str(
    "GOOGLE_CREDENTIALS_PATH",
    default=str(BASE_DIR.parent / "credentials.json"),  # legacy default
)
GOOGLE_SHEET_ID = env.str(
    "GOOGLE_SHEET_ID",
    default="1Zv9TDVJRDG_Ar-U4qTvlTcTiJ7RUpZnawxGwPpL4IZI",
)
GOOGLE_SHEET_RANGE = env.str(
    "GOOGLE_SHEET_RANGE",
    default="articulos!A1:Z1500",
)
# NOTA: los flags `SHEETS_SYNC_ENABLED` y `SHEETS_DELETE_SYNC_ENABLED`
# se movieron al singleton `configuracion.ConfiguracionGeneral` para
# que Osvaldo los pueda prender/apagar desde /admin/configuracion/
# sin redeploy. Ya no se leen del entorno.
#
# Si la env var todavía está seteada en render.yaml o en .env, no
# pasa nada (Django no la usa para nada). El código que decide si
# sincronizar lee únicamente del singleton.

# Retención de ventas. Las ventas con `fecha_compra` mayor a este
# umbral se "archivan" (soft archive: se setea `archivada_en`, no se
# borra nada). El admin las oculta del listado normal salvo que el
# operador prenda el filtro "Archivadas".
# Se aplica corriendo `manage.py archivar_ventas_antiguas` (cron).
VENTAS_RETENCION_MESES = env.int("VENTAS_RETENCION_MESES", default=18)


# ---------------------------------------------------------------------------
# WhatsApp (open-wa)
# ---------------------------------------------------------------------------
# URL del service Node.js que corre open-wa. En local apunta al
# container `wa-bot` de docker-compose. En producción habrá que poner
# la URL pública/interna del service correspondiente.
#
# Si la variable no existe, la app de campañas sigue funcionando
# (modelos, admin) pero el envío real va a fallar — eso permite usar
# el admin para preparar campañas aunque el bot esté apagado.
WHATSAPP_API_URL = env.str("WHATSAPP_API_URL", default="http://wa-bot:3000")
# Token compartido con el wa-bot. Si está vacío, el wa-bot acepta
# requests sin auth (modo dev). En producción ambos lados tienen que
# tener el MISMO valor o el bot va a rechazar todo con 401.
WHATSAPP_API_TOKEN = env.str("WHATSAPP_API_TOKEN", default="")
# Rate limit del worker: cuántos segundos esperar entre envíos.
# Open-WA recomienda al menos 3 segundos; 4-5 es más seguro para no
# levantar sospechas en WhatsApp.
WHATSAPP_DELAY_SECONDS = env.int("WHATSAPP_DELAY_SECONDS", default=4)


# ---------------------------------------------------------------------------
# MIME types (asegurar que .js sirva como JavaScript)
# ---------------------------------------------------------------------------
mimetypes.add_type("text/javascript", ".js", True)
mimetypes.add_type("application/javascript", ".js", True)


# ---------------------------------------------------------------------------
# Logging — forzar tracebacks de errores 500 a stdout
# ---------------------------------------------------------------------------
# Por default, cuando DEBUG=False Django manda los errores 500 al handler
# `mail_admins` (mail a ADMINS). Como no tenemos ADMINS seteados ni SMTP,
# los tracebacks se PIERDEN — el log de gunicorn solo muestra el access
# log con el código 500 pero sin contexto. Eso hace imposible debuggear
# desde Render sin activar DEBUG=True (que es inseguro en prod).
#
# Este config explícitamente engancha `django.request` a un StreamHandler
# que va a stdout → Render lo captura → vemos el traceback en la pestaña
# Logs sin exponer nada al usuario final.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        # django.request loguea las excepciones 500 con traceback completo.
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        # django.server: requests del runserver (dev). En prod no impacta.
        'django.server': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


# ---------------------------------------------------------------------------
# Dev-only extras
# ---------------------------------------------------------------------------
if DEBUG:
    INSTALLED_APPS.append('debug_toolbar')
    MIDDLEWARE.append('debug_toolbar.middleware.DebugToolbarMiddleware')
