"""
Aplica las ReglaCategoria activas a artículos sin categoría asignada.

Política:
  - NO pisa categorías ya asignadas. Si un artículo tiene categoría
    (aunque sea incorrecta), lo respetamos — la corrección manual
    es responsabilidad del operador.
  - Procesa todas las reglas activas en orden de `prioridad`
    ascendente (menor número = se aplica primero). Si dos reglas
    matchean el mismo artículo, gana la de menor prioridad.
  - Matching: `articulo.nombre.lower().contains(keyword.lower())`
    para cada keyword. Es feo pero suficiente para nombres cortos
    como los de un kiosco.

Modo:
  - Default: aplica en serio.
  - `--dry-run`: solo cuenta cuántos matchearía con cada categoría,
    sin escribir nada. Útil para revisar antes de tirarlo en vivo.
  - `--forzar`: pisa categorías ya asignadas. PELIGROSO — usar solo
    si Osvaldo quiere re-clasificar todo desde cero.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from articulo.models import Articulo, ReglaCategoria


class Command(BaseCommand):
    help = 'Asigna categorías a artículos según las reglas configuradas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No guarda nada, solo cuenta matches por categoría.',
        )
        parser.add_argument(
            '--forzar',
            action='store_true',
            help=(
                'Pisa categorías ya asignadas. NO usar a menos que '
                'Osvaldo quiera re-clasificar todo el catálogo.'
            ),
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        forzar = options['forzar']

        reglas = list(
            ReglaCategoria.objects
            .filter(activa=True)
            .select_related('categoria')
            .order_by('prioridad', 'id')
        )
        if not reglas:
            self.stdout.write(self.style.WARNING(
                'No hay reglas activas. Nada para aplicar.'
            ))
            return

        # Pre-procesamos: por cada regla, lista de keywords lowercase.
        reglas_lc = [
            (regla, [kw.lower() for kw in (regla.palabras_clave or []) if kw])
            for regla in reglas
        ]

        qs = Articulo.objects.all().only('id', 'nombre', 'categoria_id')
        if not forzar:
            qs = qs.filter(categoria__isnull=True)

        contador_por_cat = defaultdict(int)
        sin_match = 0
        total = qs.count()
        self.stdout.write(f'Procesando {total} artículos…')

        # iteramos en batch y guardamos con `update` puntual.
        # No usamos bulk_update porque cada articulo puede ir a una
        # categoría distinta y simplifica el conteo.
        if dry:
            for art in qs.iterator(chunk_size=500):
                cat = self._matchear(art.nombre or '', reglas_lc)
                if cat:
                    contador_por_cat[cat.nombre] += 1
                else:
                    sin_match += 1
        else:
            with transaction.atomic():
                for art in qs.iterator(chunk_size=500):
                    cat = self._matchear(art.nombre or '', reglas_lc)
                    if cat:
                        # `update` puntual: NO disparamos save() para
                        # evitar el auto-generación de codigo_interno
                        # y el signal post_save de cualquiera. Es solo
                        # un FK update.
                        Articulo.objects.filter(pk=art.pk).update(categoria=cat)
                        contador_por_cat[cat.nombre] += 1
                    else:
                        sin_match += 1

        suffix = ' (DRY RUN)' if dry else ''
        self.stdout.write(self.style.SUCCESS(f'Listo{suffix}.'))
        self.stdout.write('Asignaciones por categoría:')
        for nombre, n in sorted(contador_por_cat.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {nombre:20s}  {n}')
        self.stdout.write(f'Sin match (quedaron sin categoría): {sin_match}')

    @staticmethod
    def _matchear(nombre: str, reglas_lc):
        """
        Devuelve la primera categoría cuya regla matchee con el
        nombre, o None si ninguna. Las reglas vienen ya ordenadas
        por prioridad.
        """
        nombre_lc = nombre.lower()
        for regla, keywords in reglas_lc:
            for kw in keywords:
                if kw in nombre_lc:
                    return regla.categoria
        return None
