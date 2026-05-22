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
