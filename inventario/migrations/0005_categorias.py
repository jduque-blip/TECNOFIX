from django.db import migrations, models
import django.db.models.deletion


def textos_a_categorias(apps, schema_editor):
    """Las categorías que ya estaban escritas como texto en los productos pasan a ser categorías reales."""
    Categoria = apps.get_model('inventario', 'Categoria')
    Producto = apps.get_model('inventario', 'Producto')
    for p in Producto.objects.exclude(categoria=''):
        texto = ' '.join(p.categoria.split())
        existente = Categoria.objects.filter(nombre__iexact=texto).first()
        p.categoria_nueva = existente or Categoria.objects.create(nombre=texto)
        p.save(update_fields=['categoria_nueva'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0004_precios_sugeridos'),
    ]

    operations = [
        migrations.CreateModel(
            name='Categoria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=60, unique=True)),
            ],
            options={'verbose_name': 'categoría', 'verbose_name_plural': 'categorías', 'ordering': ['nombre']},
        ),
        migrations.AddField(
            model_name='proveedor',
            name='categorias',
            field=models.ManyToManyField(blank=True, related_name='proveedores', to='inventario.categoria', verbose_name='categorías'),
        ),
        migrations.AddField(
            model_name='producto',
            name='categoria_nueva',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='productos', to='inventario.categoria'),
        ),
        migrations.RunPython(textos_a_categorias, migrations.RunPython.noop),
        migrations.RemoveField(model_name='producto', name='categoria'),
        migrations.RenameField(model_name='producto', old_name='categoria_nueva', new_name='categoria'),
        migrations.AlterField(
            model_name='producto',
            name='categoria',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='productos', to='inventario.categoria', verbose_name='categoría'),
        ),
    ]
