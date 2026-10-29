from decimal import Decimal

from django.db import migrations, models
import django.core.validators


def fijar_iva(apps, schema_editor):
    """Las órdenes que ya existen conservan el IVA vigente hasta hoy."""
    Configuracion = apps.get_model('core', 'Configuracion')
    Orden = apps.get_model('ordenes', 'Orden')
    param = Configuracion.objects.filter(clave='IVA').first()
    iva = param.valor if param else Decimal('15')
    Orden.objects.filter(iva_porcentaje__isnull=True).update(iva_porcentaje=iva)
    # Las que ya se cerraron como canceladas o rechazadas y luego se entregaron no se pueden reconstruir; las abiertas sí tienen su estado.


class Migration(migrations.Migration):

    dependencies = [
        ('ordenes', '0014_descuento_sin_mano'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='orden', name='resultado',
            field=models.CharField(blank=True, max_length=12, verbose_name='cómo terminó (si fue cancelada o rechazada)',
                                   help_text='CANCELADA o RECHAZADA. Se conserva aunque el equipo se entregue: es lo que se muestra.'),
        ),
        migrations.AddField(
            model_name='orden', name='iva_porcentaje',
            field=models.DecimalField(blank=True, null=True, max_digits=5, decimal_places=2, verbose_name='IVA de esta orden',
                                      help_text='Se fija al crear la orden: un cambio de IVA posterior no la afecta.'),
        ),
        migrations.RunPython(fijar_iva, migrations.RunPython.noop),
    ]
