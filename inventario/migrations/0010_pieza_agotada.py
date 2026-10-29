from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('inventario', '0009_uso_precio')]

    operations = [
        migrations.AddField(
            model_name='piezasolicitada', name='agotada',
            field=models.BooleanField(default=False, verbose_name='agotada en el inventario',
                                      help_text='La pieza ya existe en el inventario pero se agotó: solo hay que reponer el stock.'),
        ),
    ]
