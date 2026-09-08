from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def recuperar_creadores_y_refinar_sugerencias(apps, schema_editor):
    Cliente = apps.get_model('cliente', 'Cliente')
    AdminLogEntry = apps.get_model('admin', 'LogEntry')
    AuditLogEntry = apps.get_model('auditlog', 'LogEntry')
    Vendedor = apps.get_model('vendedor', 'Vendedor')

    vendedores_por_usuario = dict(
        Vendedor.objects.values_list('usuario_id', 'id')
    )
    if not vendedores_por_usuario:
        return

    # django-auditlog también registra altas hechas desde Nueva venta.
    # CREATE=0. Conservamos la primera entrada por cliente.
    creador_por_cliente = {}
    audit_logs = (
        AuditLogEntry.objects
        .filter(
            content_type__app_label='cliente',
            content_type__model='cliente',
            action=0,
            actor_id__isnull=False,
        )
        .order_by('timestamp', 'pk')
        .values_list('object_pk', 'actor_id')
    )
    for object_pk, actor_id in audit_logs.iterator(chunk_size=500):
        try:
            cliente_id = int(object_pk)
        except (TypeError, ValueError):
            continue
        creador_por_cliente.setdefault(cliente_id, actor_id)

    # Como respaldo, el alta hecha desde Django admin queda registrada como
    # ADDITION=1 aunque el auditlog histórico ya se hubiera purgado.
    admin_logs = (
        AdminLogEntry.objects
        .filter(
            content_type__app_label='cliente',
            content_type__model='cliente',
            action_flag=1,
        )
        .order_by('action_time', 'pk')
        .values_list('object_id', 'user_id')
    )
    for object_id, user_id in admin_logs.iterator(chunk_size=500):
        try:
            cliente_id = int(object_id)
        except (TypeError, ValueError):
            continue
        creador_por_cliente.setdefault(cliente_id, user_id)

    for cliente_id, user_id in creador_por_cliente.items():
        vendedor_id = vendedores_por_usuario.get(user_id)
        cambios = {'creado_por_id': user_id}
        cliente = Cliente.objects.filter(pk=cliente_id).values(
            'asignacion_vendedor_confirmada',
        ).first()
        if not cliente:
            continue
        if vendedor_id and not cliente['asignacion_vendedor_confirmada']:
            cambios['vendedor_sugerido_id'] = vendedor_id
        Cliente.objects.filter(pk=cliente_id).update(**cambios)


class Migration(migrations.Migration):

    # PostgreSQL necesita cerrar la creación del FK/índice antes de que la
    # reconstrucción masiva active el historial de auditlog.
    atomic = False

    dependencies = [
        ('admin', '0003_logentry_add_action_flag_choices'),
        ('auditlog', '0015_alter_logentry_changes'),
        ('cliente', '0017_carteracliente'),
        ('vendedor', '0003_repartidor'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='creado_por',
            field=models.ForeignKey(
                blank=True,
                help_text='Usuario que dio de alta al cliente.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clientes_creados',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='vendedor_sugerido',
            field=models.ForeignKey(
                blank=True,
                help_text='Sugerencia calculada desde el creador o la venta más reciente.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clientes_sugeridos',
                to='vendedor.vendedor',
            ),
        ),
        migrations.RunPython(
            recuperar_creadores_y_refinar_sugerencias,
            migrations.RunPython.noop,
        ),
    ]
