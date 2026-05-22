# Documentación del proyecto Golosinas Insa

## Archivos

| Archivo | Para quién | Qué cubre |
|---|---|---|
| [MANUAL_OPERADOR.md](MANUAL_OPERADOR.md) | Osvaldo / quien use el admin | Paso a paso de tareas diarias: ventas, pagos, listas de precios, WhatsApp. |
| [venta-behavior.md](venta-behavior.md) | Dev (Jairo) | Behavior baseline antes del refactor de venta. Referencia técnica. |
| `capturas/` | — | Screenshots del manual del operador. |

## Cómo agregar nuevas docs

1. Crear el `.md` acá en `docs/`.
2. Linkearlo en la tabla de arriba.
3. Si necesita capturas, ponelas en `docs/capturas/` con nombre numerado (ej. `01-login.png`).
4. Commit + push.

## Convenciones

- **Idioma**: español (es el idioma del cliente final).
- **Tono**: directo, sin tecnicismos cuando es para Osvaldo. Más técnico en docs internas.
- **Capturas**: en `docs/capturas/`. Numeradas para que se ordenen visualmente al listar el directorio.
- **Versionado**: cada doc menciona la fecha de última actualización y la versión del sistema que cubre.
- **Staleness por sección**: cada sección del manual tiene una línea `> 📅 **Última revisión**: YYYY-MM-DD — ESTADO` al principio. El ESTADO se calcula automáticamente con el script de abajo.

## Verificar si la doc está desactualizada

```bash
# Ver qué secciones quedaron desactualizadas según las fechas
python3 docs/check_docs_staleness.py

# Aplicar los cambios al markdown (actualiza estados ✅ / ⚠️ / ❌)
python3 docs/check_docs_staleness.py --fix
```

Reglas que aplica el script:

| Edad de la última revisión | Estado |
|---|---|
| < 3 meses (90 días) | ✅ Actualizada |
| 3–6 meses (90–180 días) | ⚠️ Verificar |
| > 6 meses (180 días) | ❌ Desactualizada |

**Cuándo correrlo**:

- Cada vez que modificás una sección del manual, actualizá su fecha y corré `--fix`.
- Mensualmente (recordatorio en calendario), para que las secciones que no se tocaron en un tiempo se marquen automáticamente.
- Opcional: agregarlo a un pre-commit hook que falle si hay secciones ❌ Desactualizadas.
