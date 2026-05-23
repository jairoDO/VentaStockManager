"""
Helpers para normalizar números de teléfono argentinos al formato
WhatsApp (solo dígitos, prefijo internacional 54, sin `+`).

Esta lógica vive acá para que la usen:
  - `Cliente.save()` (auto-deriva `whatsapp_number` desde `telefono`)
  - El management command `backfill_whatsapp_number` (re-procesa
    clientes legacy que aún tienen `whatsapp_number=''`)
  - Cualquier otro caller que necesite normalizar a futuro

Importante: la migración `0006_backfill_whatsapp_number` tiene una
copia INLINE de esta función. Eso es deliberado — las migraciones
son frozen en el tiempo (no podemos importar models/utils del código
actual sin riesgo de bugs si la lógica cambia). NO importar desde la
migración: leer la copia inline.

Si la heurística cambia y queremos re-aplicarla, es un management
command nuevo, NO una RunPython migration.
"""

from __future__ import annotations


def normalizar_telefono_ar(telefono_raw: str) -> str:
    """
    Convierte un string de teléfono (formato libre) al formato
    WhatsApp argentino: 54 + 9 (móvil) + área + número. Devuelve ''
    si no podemos inferir con confianza.

    Reglas:
      - Solo dígitos (se ignoran +, -, espacios, paréntesis).
      - Placeholders ('00000000', solo ceros, vacío) → ''.
      - <8 dígitos → '' (demasiado corto, ambiguo).
      - Empieza con '54' y >=11 dígitos → confiamos (ya está normalizado).
      - Empieza con '0' (prefijo nacional AR) → lo sacamos.
      - Empieza con '15' sin área (≤10 dígitos) → '' (ambiguo).
      - 10 dígitos sin código → móvil AR sin internacional, prepend '549'.
      - 11+ dígitos SIN 54 → devolver TAL CUAL (asumir internacional).

    Esta heurística la diseñamos para el dump legacy de Golosinas Insa
    (formato libre, escrito a mano por años). NO es un parser robusto
    de E.164 — si en algún momento se interna en otros países, mejor
    usar python-phonenumbers (pesa más, dependencia extra).

    Histórico: antes anteponíamos '54' a cualquier número de 11+ dígitos
    sin código país AR. Eso ROMPÍA números internacionales (ej.
    `61451347124` → `5461451347124` → no existe en WA). Ahora si tiene
    11+ dígitos lo asumimos internacional. Si un usuario AR carga
    "9351345 2496" (11 digitos sin 54), va a fallar — el operador
    tiene que cargar 549... completo o solo los 10 dígitos del móvil.
    """
    if not telefono_raw:
        return ''
    digitos = ''.join(c for c in telefono_raw if c.isdigit())
    if not digitos:
        return ''
    # Placeholder del modelo legacy (default='00000000').
    if digitos == '00000000' or set(digitos) == {'0'}:
        return ''
    # Demasiado corto: probablemente local sin código de área.
    if len(digitos) < 8:
        return ''
    # Ya viene con 54: confiamos.
    if digitos.startswith('54'):
        if len(digitos) >= 11:
            return digitos
        return ''
    # Empieza con 0 (prefijo nacional AR): sacarlo.
    if digitos.startswith('0'):
        digitos = digitos.lstrip('0')
    # Empieza con 15 sin área: ambiguo, descartamos.
    if digitos.startswith('15') and len(digitos) <= 10:
        return ''
    # 10 dígitos → móvil AR sin internacional. Anteponemos 549.
    if len(digitos) == 10:
        return '549' + digitos
    # 11+ dígitos sin 54 → asumimos número internacional ya formateado,
    # devolvemos TAL CUAL. Antes hacíamos prepend de '54' acá, lo que
    # convertía 61451347124 (Australia) en 5461451347124 (no existe).
    if len(digitos) >= 11:
        return digitos
    return ''
