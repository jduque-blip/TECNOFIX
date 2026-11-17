from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('inventario', '0010_pieza_agotada')]

    operations = [
        migrations.AddField(model_name='pedido', name='stock_antes', field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='pedido', name='costo_antes', field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=10, null=True)),
        migrations.AddField(model_name='pedido', name='cantidad_original', field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='pedido', name='corregido_en', field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='pedido', name='correccion_motivo', field=models.CharField(blank=True, editable=False, max_length=150)),
    ]
