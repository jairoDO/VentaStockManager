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
}


# ---------------------------------------------------------------------------
# django-q (async tasks via DB ORM)
# ---------------------------------------------------------------------------
Q_CLUSTER = {
    'name': 'DjangoQ',
    'workers': 4,
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


# ---------------------------------------------------------------------------
# MIME types (asegurar que .js sirva como JavaScript)
# ---------------------------------------------------------------------------
mimetypes.add_type("text/javascript", ".js", True)
mimetypes.add_type("application/javascript", ".js", True)


# ---------------------------------------------------------------------------
# Dev-only extras
# ---------------------------------------------------------------------------
if DEBUG:
    INSTALLED_APPS.append('debug_toolbar')
    MIDDLEWARE.append('debug_toolbar.middleware.DebugToolbarMiddleware')
