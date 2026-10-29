from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('ordenes', '0015_resultado_iva_orden')]

    operations = [
        migrations.AddField(
            model_name='presupuestoitem', name='devuelto_en',
            field=models.DateTimeField(blank=True, null=True, verbose_name='devuelto al inventario (no se usó)'),
        ),
    ]
