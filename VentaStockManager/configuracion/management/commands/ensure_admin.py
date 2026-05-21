"""
Crea o actualiza el superusuario admin desde env vars.

Diseñado para correr en el `buildCommand` de Render (después de migrate)
así no necesitás Shell pagado para crearte el primer admin. Idempotente:
si el usuario ya existe, actualiza el password (sirve para resetear si te
lo olvidaste — cambiás la env var, redeploy, y listo).

Env vars que lee (todas opcionales):

  ADMIN_PASSWORD  → REQUERIDA para que haga algo. Si no está, no-op.
                    Esto es a propósito: en dev/test no querés que se
                    cree un admin con password random.
  ADMIN_USERNAME  → opcional, default "admin"
  ADMIN_EMAIL     → opcional, default "admin@localhost"

Por qué `ADMIN_PASSWORD` y no `PASSWORD_ADMIN` (como propuso el usuario):
convención Django/12-factor es <SCOPE>_<ATRIBUTO> (ej. DATABASE_URL,
GOOGLE_SHEET_ID). `ADMIN_PASSWORD` ordena alfabéticamente con los demás
ADMIN_* y queda clarísimo. La env var se llama como sea —  acá tomamos
el valor.

Seguridad: el password nunca se loguea (solo "se setea / se actualiza").
Si querés rotarlo, cambiás la env var en Render y redeploy.

Cómo correrlo a mano (local):
    ADMIN_PASSWORD=secreto python manage.py ensure_admin
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Crea o actualiza el superuser admin desde env vars (ADMIN_PASSWORD, ADMIN_USERNAME, ADMIN_EMAIL)."

    def handle(self, *args, **options):
        password = os.environ.get("ADMIN_PASSWORD", "").strip()
        if not password:
            # No-op silencioso. Pensado para que esté siempre en
            # buildCommand sin romper deploys locales/CI donde no
            # querés crear admin.
            self.stdout.write(
                "ensure_admin: ADMIN_PASSWORD no seteado, salteando."
            )
            return

        username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
        email = os.environ.get("ADMIN_EMAIL", "admin@localhost").strip() or "admin@localhost"

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        # Si el usuario ya existía pero perdió is_staff o is_superuser
        # (alguien lo bajó manualmente), lo re-promovemos. Es la
        # intención de esta env var: garantizar que SIEMPRE haya un
        # admin operativo con este password.
        changed = False
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True

        # Siempre re-setear el password: si rotás la env var y
        # redeployás, queremos que tome efecto. set_password() hashea
        # con el hasher actual de Django, no guarda plaintext.
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"ensure_admin: superuser '{username}' CREADO (email={email})."
            ))
        elif changed:
            self.stdout.write(self.style.SUCCESS(
                f"ensure_admin: superuser '{username}' actualizado + flags re-promovidos."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"ensure_admin: password de '{username}' actualizado."
            ))
