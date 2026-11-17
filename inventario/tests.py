from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from PIL import Image

from comunicacion.models import Notificacion
from usuarios.models import Usuario

from core.models import Configuracion

from . import precios
from .models import Categoria, HistorialPrecio, MovimientoInventario, Pedido, Producto, ProductoProveedor, Proveedor

CLAVE = 'Taller-2026-ok'


def foto(nombre='p.png', formato='PNG'):
    buf = BytesIO()
    Image.new('RGB', (10, 10), 'red').save(buf, formato)
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type='image/png')


class Base(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin Uno', CLAVE)
        self.cli = Usuario.objects.create_user('c@t.com', 'Cli Uno', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@t.com', 'Tec Uno', CLAVE, rol='TECNICO')
        self.p1 = Proveedor.objects.create(nombre='Compu Mundo', telefono='0991234567')
        self.p2 = Proveedor.objects.create(nombre='Tecno Import')
        self.cat = Categoria.objects.create(nombre='Almacenamiento')
        self.client.force_login(self.admin)

    def datos(self, **extra):
        d = {
            'nombre': 'SSD 480GB', 'codigo': 'ssd-480', 'marca': 'Kingston', 'categoria': self.cat.pk,
            'precio_venta': '45', 'costo_pedido': '', 'stock_minimo': '3', 'stock_inicial': '5',
            'margen_propio': '', 'proveedor1': self.p1.pk, 'precio1': '30', 'proveedor2': self.p2.pk, 'precio2': '28.50', 'activo': 'on',
        }
        d.update(extra)
        return d

    def crear(self, **extra):
        if 'nombre' not in extra and extra.get('codigo'):
            extra['nombre'] = f"SSD 480GB {extra['codigo']}"  # el nombre+marca no se repite
        r = self.client.post(reverse('producto_nuevo'), self.datos(**extra))
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        return Producto.objects.get(codigo=self.datos(**extra)['codigo'].upper())


class PermisosTests(Base):
    URLS = ['inventario', 'producto_nuevo', 'movimientos', 'proveedores']

    def test_anonimo_va_al_inicio_de_sesion(self):
        self.client.logout()
        for nombre in self.URLS:
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 302, nombre)

    def test_cliente_y_tecnico_no_entran(self):
        for u in (self.cli, self.tec):
            self.client.force_login(u)
            for nombre in self.URLS:
                r = self.client.get(reverse(nombre))
                self.assertRedirects(r, reverse('panel'), fetch_redirect_response=False)

    def test_cliente_no_puede_crear(self):
        self.client.force_login(self.cli)
        self.client.post(reverse('producto_nuevo'), self.datos())
        self.assertFalse(Producto.objects.exists())

    def test_admin_ve_todas_las_pantallas(self):
        prod = self.crear()
        for nombre in self.URLS:
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        self.assertEqual(self.client.get(reverse('producto_detalle', args=[prod.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('producto_editar', args=[prod.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('proveedor_editar', args=[self.p1.pk])).status_code, 200)

    def test_get_no_cambia_nada(self):
        prod = self.crear()
        self.client.get(reverse('producto_activar', args=[prod.pk]))
        self.client.get(reverse('proveedor_eliminar', args=[self.p2.pk]))
        prod.refresh_from_db()
        self.assertTrue(prod.activo)
        self.assertTrue(Proveedor.objects.filter(pk=self.p2.pk).exists())


class ProductoTests(Base):
    def test_crea_con_stock_inicial_y_movimiento(self):
        prod = self.crear()
        self.assertEqual(prod.codigo, 'SSD-480')
        self.assertEqual(prod.stock, 5)
        m = prod.movimientos.get()
        self.assertEqual((m.tipo, m.cantidad, m.stock_resultante, m.usuario), ('ENTRADA', 5, 5, self.admin))
        self.assertEqual(prod.cotizaciones.count(), 2)

    def test_costo_de_pedido_vacio_toma_el_proveedor_mas_barato(self):
        self.assertEqual(self.crear().costo_pedido, Decimal('28.50'))

    def test_ganancia(self):
        self.assertEqual(self.crear().ganancia, Decimal('16.50'))

    def test_sin_stock_inicial_no_crea_movimiento(self):
        prod = self.crear(stock_inicial='')
        self.assertEqual(prod.stock, 0)
        self.assertEqual(prod.movimientos.count(), 0)

    def test_codigo_repetido_sin_distinguir_mayusculas(self):
        self.crear()
        r = self.client.post(reverse('producto_nuevo'), self.datos(nombre='Otro'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('codigo', r.context['form'].errors)
        self.assertEqual(Producto.objects.count(), 1)

    def test_codigo_con_espacios_es_invalido(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(codigo='ssd 480'))
        self.assertIn('codigo', r.context['form'].errors)

    def test_proveedor_sin_precio(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(precio2=''))
        self.assertIn('precio2', r.context['form'].errors)

    def test_precio_sin_proveedor(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(proveedor2=''))
        self.assertIn('proveedor2', r.context['form'].errors)

    def test_mismo_proveedor_dos_veces(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(proveedor2=self.p1.pk))
        self.assertIn('proveedor2', r.context['form'].errors)

    def test_precio_de_proveedor_cero(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(precio1='0'))
        self.assertIn('precio1', r.context['form'].errors)

    def test_venta_menor_que_costo_de_pedido(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(precio_venta='20'))
        self.assertIn('precio_venta', r.context['form'].errors)

    def test_precio_venta_cero_o_negativo(self):
        for v in ('0', '-5', 'abc'):
            r = self.client.post(reverse('producto_nuevo'), self.datos(precio_venta=v))
            self.assertIn('precio_venta', r.context['form'].errors, v)

    def test_stock_negativo_no_se_acepta(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(stock_inicial='-1', stock_minimo='-2'))
        self.assertIn('stock_inicial', r.context['form'].errors)
        self.assertIn('stock_minimo', r.context['form'].errors)

    def test_imagen_valida_e_invalida(self):
        r = self.client.post(reverse('producto_nuevo'), {**self.datos(codigo='IMG-1'), 'imagen': foto()})
        self.assertEqual(r.status_code, 302)
        malo = SimpleUploadedFile('x.png', b'no soy imagen', content_type='image/png')
        r = self.client.post(reverse('producto_nuevo'), {**self.datos(codigo='IMG-2'), 'imagen': malo})
        self.assertIn('imagen', r.context['form'].errors)
        self.assertFalse(Producto.objects.filter(codigo='IMG-2').exists())

    def test_editar_reemplaza_proveedores_sin_tocar_stock(self):
        prod = self.crear()
        d = self.datos(proveedor2='', precio2='', precio1='31', costo_pedido='29')
        del d['stock_inicial']
        r = self.client.post(reverse('producto_editar', args=[prod.pk]), d)
        self.assertEqual(r.status_code, 302)
        prod.refresh_from_db()
        self.assertEqual(prod.stock, 5)
        self.assertEqual(prod.costo_pedido, Decimal('31'))  # su único proveedor ahora cobra 31
        self.assertEqual(list(prod.cotizaciones.values_list('precio', flat=True)), [Decimal('31.00')])
        self.assertEqual(prod.movimientos.count(), 1)

    def test_editar_mantiene_su_propio_codigo(self):
        prod = self.crear()
        d = self.datos()
        del d['stock_inicial']
        self.assertEqual(self.client.post(reverse('producto_editar', args=[prod.pk]), d).status_code, 302)

    def test_modelo_no_permite_tercer_proveedor(self):
        prod = self.crear()
        p3 = Proveedor.objects.create(nombre='Tercero')
        with self.assertRaises(Exception):
            ProductoProveedor.objects.create(producto=prod, proveedor=p3, precio=10)

    def test_desactivar_y_activar(self):
        prod = self.crear()
        self.client.post(reverse('producto_activar', args=[prod.pk]))
        prod.refresh_from_db()
        self.assertFalse(prod.activo)
        self.client.post(reverse('producto_activar', args=[prod.pk]))
        prod.refresh_from_db()
        self.assertTrue(prod.activo)


class ReposicionTests(Base):
    def setUp(self):
        super().setUp()
        self.prod = self.crear()
        self.url = reverse('producto_detalle', args=[self.prod.pk])

    def reponer(self, **extra):
        d = {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '10', 'precio_unit': '32', 'observacion': 'Pedido semanal'}
        d.update(extra)
        return self.client.post(self.url, d)

    def test_reponer_suma_stock_actualiza_costo_y_registra(self):
        self.assertRedirects(self.reponer(), self.url, fetch_redirect_response=False)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 15)
        self.assertEqual(self.prod.costo_pedido, Decimal('30.83'))  # promedio: 5 a 28.50 y 10 a 32
        pedido = Pedido.objects.get()
        self.assertEqual((pedido.cantidad, pedido.costo_total, pedido.usuario), (10, Decimal('320.00'), self.admin))
        m = self.prod.movimientos.first()
        self.assertEqual((m.tipo, m.cantidad, m.stock_resultante, m.pedido), ('ENTRADA', 10, 15, pedido))

    def test_proveedor_que_no_es_del_producto(self):
        otro = Proveedor.objects.create(nombre='Ajeno SA')
        r = self.reponer(proveedor=otro.pk)
        self.assertEqual(r.status_code, 200)
        self.assertIn('proveedor', r.context['f_reponer'].errors)
        self.assertFalse(Pedido.objects.exists())

    def test_cantidad_invalida(self):
        for c in ('0', '-3', 'x', '', '999999'):
            r = self.reponer(cantidad=c)
            self.assertEqual(r.status_code, 200, c)
            self.assertIn('cantidad', r.context['f_reponer'].errors, c)
        self.assertFalse(Pedido.objects.exists())

    def test_precio_invalido(self):
        for v in ('0', '-1', 'abc'):
            self.assertIn('precio_unit', self.reponer(precio_unit=v).context['f_reponer'].errors, v)

    def test_cliente_no_puede_reponer(self):
        self.client.force_login(self.cli)
        self.reponer()
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 5)

    def test_producto_sin_proveedores_no_ofrece_formulario(self):
        sin = Producto.objects.create(nombre='Cable', codigo='CAB-1', precio_venta=5)
        r = self.client.get(reverse('producto_detalle', args=[sin.pk]))
        self.assertContains(r, 'no tiene proveedores')


class AjusteTests(Base):
    def setUp(self):
        super().setUp()
        self.prod = self.crear()
        self.url = reverse('producto_detalle', args=[self.prod.pk])

    def ajustar(self, real, motivo='Conteo físico'):
        return self.client.post(self.url, {'accion': 'ajustar', 'stock_real': real, 'motivo': motivo})

    def test_ajuste_baja_y_registra_diferencia(self):
        self.ajustar('4')
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 4)
        m = self.prod.movimientos.first()
        self.assertEqual((m.tipo, m.cantidad, m.stock_resultante, m.motivo), ('AJUSTE', -1, 4, 'Conteo físico'))

    def test_ajuste_sube(self):
        self.ajustar('9')
        self.assertEqual(self.prod.movimientos.first().cantidad, 4)

    def test_sin_cambio_se_rechaza(self):
        r = self.ajustar('5')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.prod.movimientos.count(), 1)

    def test_motivo_obligatorio_y_stock_no_negativo(self):
        self.assertIn('motivo', self.ajustar('4', motivo=' ').context['f_ajuste'].errors)
        self.assertIn('stock_real', self.ajustar('-1').context['f_ajuste'].errors)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 5)

    def test_stock_bajo_avisa_al_admin(self):
        self.ajustar('2')  # mínimo 3
        n = Notificacion.objects.get(usuario=self.admin)
        self.assertIn('Stock bajo', n.texto)
        self.assertEqual(n.enlace, self.url)

    def test_sin_stock_bajo_no_avisa(self):
        self.ajustar('8')
        self.assertFalse(Notificacion.objects.exists())

    def test_producto_inactivo_no_avisa(self):
        self.prod.activo = False
        self.prod.save()
        self.ajustar('1')
        self.assertFalse(Notificacion.objects.exists())


class ListaTests(Base):
    def setUp(self):
        super().setUp()
        self.c_pant = Categoria.objects.create(nombre='Pantallas')
        self.c_peri = Categoria.objects.create(nombre='Periféricos')
        self.a = self.crear(nombre='Pantalla LCD', codigo='LCD-1', marca='Samsung', categoria=self.c_pant.pk, stock_inicial='1')
        self.b = self.crear(nombre='Teclado', codigo='TEC-9', marca='Logitech', categoria=self.c_peri.pk, stock_inicial='20')

    def nombres(self, **params):
        r = self.client.get(reverse('inventario'), params)
        return [p.nombre for p in r.context['pagina']]

    def test_busqueda_por_nombre_codigo_marca_y_categoria(self):
        for q in ('lcd', 'LCD-1', 'samsung', 'pantallas'):
            self.assertEqual(self.nombres(q=q), ['Pantalla LCD'], q)
        self.assertEqual(self.nombres(q='zzz'), [])

    def test_filtro_stock_bajo(self):
        self.assertEqual(self.nombres(filtro='bajo'), ['Pantalla LCD'])
        self.assertEqual(sorted(self.nombres()), ['Pantalla LCD', 'Teclado'])

    def test_stock_bajo_ignora_desactivados(self):
        self.a.activo = False
        self.a.save()
        self.assertEqual(self.nombres(filtro='bajo'), [])

    def test_filtro_raro_se_ignora(self):
        self.assertEqual(len(self.nombres(filtro='<script>')), 2)

    def test_paginacion(self):
        for i in range(12):
            Producto.objects.create(nombre=f'Extra {i}', codigo=f'EX-{i}', precio_venta=1)
        r = self.client.get(reverse('inventario'))
        self.assertEqual(len(r.context['pagina']), 10)
        self.assertEqual(self.client.get(reverse('inventario'), {'pagina': 'x'}).status_code, 200)

    def test_movimientos_filtros(self):
        self.client.post(reverse('producto_detalle', args=[self.b.pk]), {'accion': 'ajustar', 'stock_real': '18', 'motivo': 'Se dañaron 2'})
        r = self.client.get(reverse('movimientos'), {'tipo': 'AJUSTE'})
        self.assertEqual([m.producto for m in r.context['pagina']], [self.b])
        r = self.client.get(reverse('movimientos'), {'q': 'LCD-1'})
        self.assertEqual([m.producto for m in r.context['pagina']], [self.a])
        r = self.client.get(reverse('movimientos'), {'tipo': 'otro'})
        self.assertEqual(r.context['pagina'].paginator.count, 3)


class ProveedorTests(Base):
    def test_crear_y_nombre_repetido(self):
        self.client.post(reverse('proveedores'), {'nombre': 'Nuevo Prov', 'telefono': '04 212 3456'})
        self.assertEqual(Proveedor.objects.get(nombre='Nuevo Prov').telefono, '042123456')
        r = self.client.post(reverse('proveedores'), {'nombre': 'nuevo prov', 'telefono': ''})
        self.assertIn('nombre', r.context['form'].errors)

    def test_telefono_invalido(self):
        r = self.client.post(reverse('proveedores'), {'nombre': 'Otro', 'telefono': '123'})
        self.assertIn('telefono', r.context['form'].errors)

    def test_editar(self):
        self.client.post(reverse('proveedor_editar', args=[self.p2.pk]), {'nombre': 'Tecno Import SA', 'telefono': '+593991112233'})
        self.p2.refresh_from_db()
        self.assertEqual((self.p2.nombre, self.p2.telefono), ('Tecno Import SA', '0991112233'))

    def test_eliminar_sin_pedidos(self):
        self.client.post(reverse('proveedor_eliminar', args=[self.p2.pk]))
        self.assertFalse(Proveedor.objects.filter(pk=self.p2.pk).exists())

    def test_no_se_elimina_con_pedidos(self):
        prod = self.crear()
        self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '2', 'precio_unit': '30'})
        r = self.client.post(reverse('proveedor_eliminar', args=[self.p1.pk]), follow=True)
        self.assertTrue(Proveedor.objects.filter(pk=self.p1.pk).exists())
        self.assertContains(r, 'ya tiene pedidos')


class RestriccionesTests(Base):
    def test_movimiento_en_cero_o_entrada_negativa_no_entra(self):
        prod = self.crear()
        for tipo, cant in (('AJUSTE', 0), ('ENTRADA', -1), ('SALIDA', 0)):
            with self.assertRaises(IntegrityError), transaction.atomic():
                MovimientoInventario.objects.create(producto=prod, tipo=tipo, cantidad=cant, stock_resultante=1)

    def test_no_se_borra_producto_con_movimientos(self):
        from django.db.models.deletion import ProtectedError
        prod = self.crear()
        with self.assertRaises(ProtectedError):
            prod.delete()


class PreciosTests(TestCase):
    def test_sugerido_redondea_hacia_arriba_de_5_en_5(self):
        self.assertEqual(precios.sugerido(Decimal('28.50'), 30), Decimal('37.05'))
        self.assertEqual(precios.sugerido(Decimal('10'), 30), Decimal('13.00'))
        self.assertEqual(precios.sugerido(Decimal('10.01'), 30), Decimal('13.05'))
        self.assertEqual(precios.sugerido(Decimal('8.50'), 0), Decimal('8.50'))

    def test_sin_costo_no_hay_sugerido(self):
        self.assertIsNone(precios.sugerido(Decimal('0'), 30))
        self.assertIsNone(precios.sugerido(None, 30))
        self.assertIsNone(precios.margen_real(Decimal('10'), Decimal('0')))

    def test_margen_real(self):
        self.assertEqual(precios.margen_real(Decimal('45'), Decimal('30')), Decimal('50.0'))
        self.assertEqual(precios.margen_real(Decimal('37.05'), Decimal('28.50')), Decimal('30.0'))
        self.assertEqual(precios.margen_real(Decimal('20'), Decimal('30')), Decimal('-33.3'))


class MargenTests(Base):
    def test_margen_general_por_defecto_30(self):
        self.assertEqual(Configuracion.margen_defecto(), Decimal('30'))

    def test_admin_cambia_el_margen_general(self):
        self.client.post(reverse('margen_guardar'), {'valor': '40'})
        self.assertEqual(Configuracion.margen_defecto(), Decimal('40'))

    def test_margen_invalido_se_rechaza(self):
        for v in ('-1', '501', 'x', ''):
            self.client.post(reverse('margen_guardar'), {'valor': v})
        self.assertEqual(Configuracion.margen_defecto(), Decimal('30'))

    def test_otros_roles_no_cambian_el_margen(self):
        for u in (self.cli, self.tec):
            self.client.force_login(u)
            self.client.post(reverse('margen_guardar'), {'valor': '90'})
        self.assertEqual(Configuracion.margen_defecto(), Decimal('30'))

    def test_get_no_cambia_el_margen(self):
        self.client.get(reverse('margen_guardar'))
        self.assertEqual(Configuracion.margen_defecto(), Decimal('30'))

    def test_producto_usa_el_margen_general_o_el_propio(self):
        prod = self.crear()  # costo 28.50
        self.assertEqual(prod.margen_objetivo, Decimal('30'))
        self.assertEqual(prod.precio_sugerido, Decimal('37.05'))
        Configuracion.objects.update_or_create(clave=Configuracion.MARGEN, defaults={'valor': Decimal('50')})
        self.assertEqual(prod.precio_sugerido, Decimal('42.75'))
        prod.margen_propio = Decimal('10')
        self.assertEqual(prod.precio_sugerido, Decimal('31.35'))

    def test_margen_propio_se_valida_y_se_guarda(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(margen_propio='600'))
        self.assertIn('margen_propio', r.context['form'].errors)
        prod = self.crear(margen_propio='35')
        self.assertEqual(prod.margen_propio, Decimal('35'))

    def test_bajo_objetivo(self):
        prod = self.crear(precio_venta='45')  # costo 28.50, sugerido 37.05
        self.assertFalse(prod.bajo_objetivo)
        prod.precio_venta = Decimal('30')
        self.assertTrue(prod.bajo_objetivo)

    def test_usar_sugerido_cambia_el_costo_de_venta(self):
        prod = self.crear(precio_venta='30')
        url = reverse('producto_detalle', args=[prod.pk])
        self.assertContains(self.client.get(url), 'Usar el sugerido')
        self.client.post(url, {'accion': 'usar_sugerido'})
        prod.refresh_from_db()
        self.assertEqual(prod.precio_venta, Decimal('37.05'))
        self.assertNotContains(self.client.get(url), 'Usar el sugerido')

    def test_usar_sugerido_sin_costo_no_hace_nada(self):
        sin = Producto.objects.create(nombre='Cable', codigo='CAB-1', precio_venta=5)
        self.client.post(reverse('producto_detalle', args=[sin.pk]), {'accion': 'usar_sugerido'})
        sin.refresh_from_db()
        self.assertEqual(sin.precio_venta, Decimal('5'))

    def test_cliente_no_puede_usar_sugerido(self):
        prod = self.crear(precio_venta='30')
        self.client.force_login(self.cli)
        self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'usar_sugerido'})
        prod.refresh_from_db()
        self.assertEqual(prod.precio_venta, Decimal('30'))

    def test_detalle_muestra_comparador_por_proveedor(self):
        prod = self.crear()
        r = self.client.get(reverse('producto_detalle', args=[prod.pk]))
        filas = r.context['cotizaciones']
        self.assertEqual([f['cot'].proveedor for f in filas], [self.p2, self.p1])  # el más barato primero
        self.assertEqual(filas[0]['sugerido'], Decimal('37.05'))
        self.assertEqual(filas[1]['sugerido'], Decimal('39.00'))
        self.assertEqual(filas[0]['ganancia'], Decimal('16.50'))


class HistorialPreciosTests(Base):
    def test_crear_registra_el_precio_de_cada_proveedor(self):
        prod = self.crear()
        self.assertEqual(prod.historial_precios.count(), 2)

    def test_editar_sin_cambiar_precios_no_duplica_historial(self):
        prod = self.crear()
        d = self.datos()
        del d['stock_inicial']
        self.client.post(reverse('producto_editar', args=[prod.pk]), d)
        self.assertEqual(prod.historial_precios.count(), 2)

    def test_editar_con_precio_nuevo_actualiza_fecha_e_historial(self):
        prod = self.crear()
        antes = prod.cotizaciones.get(proveedor=self.p1).actualizado_en
        d = self.datos(precio1='33')
        del d['stock_inicial']
        self.client.post(reverse('producto_editar', args=[prod.pk]), d)
        c = prod.cotizaciones.get(proveedor=self.p1)
        self.assertEqual(c.precio, Decimal('33'))
        self.assertGreater(c.actualizado_en, antes)
        self.assertEqual(prod.historial_precios.filter(proveedor=self.p1).count(), 2)

    def test_cambiar_un_proveedor_por_otro(self):
        prod = self.crear()
        p3 = Proveedor.objects.create(nombre='Tercero SA')
        d = self.datos(proveedor2=p3.pk, precio2='27')
        del d['stock_inicial']
        r = self.client.post(reverse('producto_editar', args=[prod.pk]), d)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(set(prod.cotizaciones.values_list('proveedor', flat=True)), {self.p1.pk, p3.pk})

    def test_pedido_con_precio_distinto_actualiza_la_cotizacion(self):
        prod = self.crear()
        self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '4', 'precio_unit': '34'})
        self.assertEqual(prod.cotizaciones.get(proveedor=self.p1).precio, Decimal('34'))
        h = prod.historial_precios.filter(proveedor=self.p1).first()
        self.assertEqual((h.precio, h.origen), (Decimal('34'), 'PEDIDO'))

    def test_pedido_al_mismo_precio_no_ensucia_el_historial(self):
        prod = self.crear()
        self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '4', 'precio_unit': '30'})
        self.assertEqual(prod.historial_precios.filter(proveedor=self.p1).count(), 1)

    def test_aviso_cuando_el_costo_sube(self):
        prod = self.crear()  # costo de pedido 28.50
        r = self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '4', 'precio_unit': '40'}, follow=True)
        self.assertContains(r, 'Costo promedio recalculado: de $28.50 a $33.61')
        self.assertContains(r, 'Precio sugerido')

    def test_sin_aviso_si_el_costo_baja(self):
        prod = self.crear()
        r = self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'reponer', 'proveedor': self.p2.pk, 'cantidad': '4', 'precio_unit': '25'}, follow=True)
        self.assertContains(r, 'Costo promedio recalculado: de $28.50 a $26.94')  # 5 a 28.50 y 4 a 25
        self.assertNotContains(r, 'Puedes pulsar')


class DiasEntregaTests(Base):
    def test_se_guarda_y_se_muestra(self):
        self.client.post(reverse('proveedores'), {'nombre': 'Rapidito', 'telefono': '', 'dias_entrega': '3'})
        v = Proveedor.objects.get(nombre='Rapidito')
        self.assertEqual(v.dias_entrega, 3)
        self.assertContains(self.client.get(reverse('proveedores')), 'value="3"')

    def test_es_opcional(self):
        self.client.post(reverse('proveedores'), {'nombre': 'Sin dias', 'telefono': '', 'dias_entrega': ''})
        self.assertIsNone(Proveedor.objects.get(nombre='Sin dias').dias_entrega)

    def test_invalidos(self):
        for v in ('-1', '400', 'abc'):
            r = self.client.post(reverse('proveedores'), {'nombre': 'Malo', 'telefono': '', 'dias_entrega': v})
            self.assertIn('dias_entrega', r.context['form'].errors, v)
        self.assertFalse(Proveedor.objects.filter(nombre='Malo').exists())

    def test_aparece_en_el_comparador(self):
        self.p1.dias_entrega = 2
        self.p1.save()
        prod = self.crear()
        self.assertContains(self.client.get(reverse('producto_detalle', args=[prod.pk])), 'Entrega en 2 días')


class CategoriasTests(Base):
    def test_crear_categoria_y_nombre_repetido(self):
        self.client.post(reverse('categoria_crear'), {'nombre': 'Monitores'})
        self.assertTrue(Categoria.objects.filter(nombre='Monitores').exists())
        self.client.post(reverse('categoria_crear'), {'nombre': 'monitores'})
        self.assertEqual(Categoria.objects.filter(nombre__iexact='monitores').count(), 1)

    def test_nombre_muy_corto(self):
        self.client.post(reverse('categoria_crear'), {'nombre': 'ab'})
        self.assertFalse(Categoria.objects.filter(nombre='ab').exists())

    def test_eliminar_deja_productos_sin_categoria(self):
        prod = self.crear()
        self.assertEqual(prod.categoria, self.cat)
        self.client.post(reverse('categoria_eliminar', args=[self.cat.pk]))
        prod.refresh_from_db()
        self.assertIsNone(prod.categoria)

    def test_solo_admin_y_solo_post(self):
        self.client.force_login(self.cli)
        self.client.post(reverse('categoria_crear'), {'nombre': 'Intruso'})
        self.client.post(reverse('categoria_eliminar', args=[self.cat.pk]))
        self.assertFalse(Categoria.objects.filter(nombre='Intruso').exists())
        self.assertTrue(Categoria.objects.filter(pk=self.cat.pk).exists())
        self.client.force_login(self.admin)
        self.client.get(reverse('categoria_eliminar', args=[self.cat.pk]))
        self.assertTrue(Categoria.objects.filter(pk=self.cat.pk).exists())

    def test_producto_con_categoria_inexistente(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(categoria=9999))
        self.assertIn('categoria', r.context['form'].errors)

    def test_filtrar_productos_por_categoria(self):
        otra = Categoria.objects.create(nombre='Cables')
        self.crear()
        self.crear(nombre='Cable HDMI', codigo='HDMI-1', categoria=otra.pk)
        r = self.client.get(reverse('inventario'), {'categoria': otra.pk})
        self.assertEqual([p.nombre for p in r.context['pagina']], ['Cable HDMI'])
        r = self.client.get(reverse('inventario'), {'categoria': 'x'})
        self.assertEqual(len(r.context['pagina']), 2)

    def test_paginacion_conserva_filtros(self):
        for i in range(12):
            Producto.objects.create(nombre=f'Bajo {i}', codigo=f'B-{i}', precio_venta=1, categoria=self.cat)
        r = self.client.get(reverse('inventario'), {'filtro': 'bajo', 'categoria': self.cat.pk})
        self.assertContains(r, f'filtro=bajo&amp;categoria={self.cat.pk}')


class ProveedoresPorCategoriaTests(Base):
    def test_crear_proveedor_con_categorias(self):
        c2 = Categoria.objects.create(nombre='Celulares')
        self.client.post(reverse('proveedores'), {'nombre': 'Movil Express', 'telefono': '0987654321', 'dias_entrega': '2', 'categorias': [self.cat.pk, c2.pk]})
        v = Proveedor.objects.get(nombre='Movil Express')
        self.assertEqual(set(v.categorias.all()), {self.cat, c2})

    def test_editar_categorias(self):
        self.p1.categorias.add(self.cat)
        self.client.post(reverse('proveedor_editar', args=[self.p1.pk]), {'nombre': 'Compu Mundo', 'telefono': '0991234567', 'dias_entrega': '', 'categorias': []})
        self.assertEqual(self.p1.categorias.count(), 0)

    def test_filtrar_por_categoria(self):
        self.p1.categorias.add(self.cat)
        r = self.client.get(reverse('proveedores'), {'categoria': self.cat.pk})
        self.assertEqual([v.nombre for v in r.context['proveedores']], ['Compu Mundo'])
        r = self.client.get(reverse('proveedores'))
        self.assertEqual(len(r.context['proveedores']), 2)

    def test_editar_telefono_y_dias_en_la_lista(self):
        self.client.post(reverse('proveedor_contacto', args=[self.p2.pk]), {'telefono': '0991112233', 'dias_entrega': '5'})
        self.p2.refresh_from_db()
        self.assertEqual((self.p2.telefono, self.p2.dias_entrega), ('0991112233', 5))

    def test_contacto_invalido_no_guarda(self):
        self.client.post(reverse('proveedor_contacto', args=[self.p1.pk]), {'telefono': '123', 'dias_entrega': '5'})
        self.client.post(reverse('proveedor_contacto', args=[self.p1.pk]), {'telefono': '0991234567', 'dias_entrega': '999'})
        self.p1.refresh_from_db()
        self.assertEqual((self.p1.telefono, self.p1.dias_entrega), ('0991234567', None))

    def test_contacto_vacio_borra_telefono_y_dias(self):
        self.p1.dias_entrega = 3
        self.p1.save()
        self.client.post(reverse('proveedor_contacto', args=[self.p1.pk]), {'telefono': '', 'dias_entrega': ''})
        self.p1.refresh_from_db()
        self.assertEqual((self.p1.telefono, self.p1.dias_entrega), ('', None))

    def test_contacto_solo_admin_y_post(self):
        self.client.force_login(self.tec)
        self.client.post(reverse('proveedor_contacto', args=[self.p1.pk]), {'telefono': '0999999999', 'dias_entrega': '1'})
        self.client.force_login(self.admin)
        self.client.get(reverse('proveedor_contacto', args=[self.p1.pk]))
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.telefono, '0991234567')

    def test_redireccion_externa_se_ignora(self):
        r = self.client.post(reverse('proveedor_contacto', args=[self.p1.pk]), {'telefono': '', 'volver': 'https://malo.com'})
        self.assertRedirects(r, reverse('proveedores'), fetch_redirect_response=False)

    def test_etiqueta_en_el_formulario_de_producto(self):
        self.p1.categorias.add(self.cat)
        r = self.client.get(reverse('producto_nuevo'))
        self.assertContains(r, 'Compu Mundo (Almacenamiento)')


class CargarBaseTests(TestCase):
    def test_carga_categorias_y_proveedores_sin_telefono(self):
        from django.core.management import call_command
        call_command('cargar_base', verbosity=0)
        self.assertTrue(Proveedor.objects.filter(nombre='Cartimex').exists())
        cartimex = Proveedor.objects.get(nombre='Cartimex')
        self.assertIn('Impresoras', [c.nombre for c in cartimex.categorias.all()])
        self.assertEqual(Proveedor.objects.exclude(telefono='').count(), 0)

    def test_no_pisa_lo_que_el_admin_cambio(self):
        from django.core.management import call_command
        call_command('cargar_base', verbosity=0)
        v = Proveedor.objects.get(nombre='Cartimex')
        v.telefono = '042000000'
        v.save()
        v.categorias.clear()
        call_command('cargar_base', verbosity=0)
        v.refresh_from_db()
        self.assertEqual(v.telefono, '042000000')
        self.assertEqual(v.categorias.count(), 0)
        self.assertEqual(Proveedor.objects.filter(nombre='Cartimex').count(), 1)


class MigracionCategoriasTests(TransactionTestCase):
    """Los productos que ya tenían la categoría escrita como texto la conservan como categoría real."""

    def test_textos_pasan_a_categorias_reales(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        antes = [('inventario', '0004_precios_sugeridos')]
        despues = [('inventario', '0005_categorias')]
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(antes)
        try:
            viejo = ejecutor.loader.project_state(antes).apps
            ProductoViejo = viejo.get_model('inventario', 'Producto')
            for codigo, texto in (('A-1', 'Pantallas'), ('A-2', ' pantallas '), ('A-3', 'Cables'), ('A-4', '')):
                ProductoViejo.objects.create(nombre='P ' + codigo, codigo=codigo, categoria=texto, precio_venta=Decimal('5'))
            ejecutor = MigrationExecutor(connection)
            ejecutor.migrate(despues)
            nuevo = ejecutor.loader.project_state(despues).apps
            Producto_, Categoria_ = nuevo.get_model('inventario', 'Producto'), nuevo.get_model('inventario', 'Categoria')
            self.assertEqual(sorted(Categoria_.objects.values_list('nombre', flat=True)), ['Cables', 'Pantallas'])
            self.assertEqual(Producto_.objects.get(codigo='A-1').categoria_id, Producto_.objects.get(codigo='A-2').categoria_id)
            self.assertEqual(Producto_.objects.get(codigo='A-3').categoria.nombre, 'Cables')
            self.assertIsNone(Producto_.objects.get(codigo='A-4').categoria_id)
        finally:
            MigrationExecutor(connection).loader.build_graph()
            ejecutor = MigrationExecutor(connection)
            ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())


class FormularioProductoTests(Base):
    def test_el_costo_de_pedido_es_el_del_proveedor_mas_barato_aunque_se_escriba_otro(self):
        prod = self.crear(costo_pedido='99')
        self.assertEqual(prod.costo_pedido, Decimal('28.50'))

    def test_sin_proveedores_se_usa_el_costo_escrito(self):
        prod = self.crear(proveedor1='', precio1='', proveedor2='', precio2='', costo_pedido='12', precio_venta='20')
        self.assertEqual(prod.costo_pedido, Decimal('12'))

    def test_costo_de_venta_vacio_se_calcula_con_el_margen(self):
        prod = self.crear(precio_venta='')
        self.assertEqual(prod.precio_venta, Decimal('37.05'))  # 28.50 + 30 %

    def test_costo_de_venta_vacio_usa_el_margen_escrito(self):
        prod = self.crear(precio_venta='', margen_propio='50')
        self.assertEqual(prod.precio_venta, Decimal('42.75'))
        self.assertEqual(prod.margen_propio, Decimal('50'))

    def test_costo_de_venta_vacio_sin_costo_da_error(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(precio_venta='', proveedor1='', precio1='', proveedor2='', precio2=''))
        self.assertIn('precio_venta', r.context['form'].errors)

    def test_costo_de_venta_escrito_se_respeta(self):
        self.assertEqual(self.crear(precio_venta='41.99').precio_venta, Decimal('41.99'))

    def test_margen_igual_al_general_no_se_guarda_como_propio(self):
        self.assertIsNone(self.crear(margen_propio='30').margen_propio)
        self.assertEqual(self.crear(codigo='otro-2', margen_propio='35').margen_propio, Decimal('35'))

    def test_el_formulario_nuevo_arranca_con_el_margen_general(self):
        r = self.client.get(reverse('producto_nuevo'))
        self.assertEqual(r.context['form'].initial['margen_propio'], Decimal('30'))
        Configuracion.objects.update_or_create(clave=Configuracion.MARGEN, defaults={'valor': Decimal('40')})
        r = self.client.get(reverse('producto_nuevo'))
        self.assertEqual(r.context['form'].initial['margen_propio'], Decimal('40'))

    def test_proveedores_van_antes_que_los_costos_y_marcan_sus_categorias(self):
        self.p1.categorias.add(self.cat)
        html = self.client.get(reverse('producto_nuevo')).content.decode()
        self.assertLess(html.index('Proveedores y el precio que cobra cada uno'), html.index('Costo de pedido (el del proveedor'))
        self.assertLess(html.index('Proveedores y el precio que cobra cada uno'), html.index('Costo de venta (al cliente)'))
        self.assertIn(f'data-cats="{self.cat.pk}"', html)
        self.assertIn('data-categoria', html)

    def test_editar_sin_cambiar_proveedores_respeta_el_costo_del_ultimo_pedido(self):
        prod = self.crear()
        self.client.post(reverse('producto_detalle', args=[prod.pk]), {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '2', 'precio_unit': '30'})
        prod.refresh_from_db()
        self.assertEqual(prod.costo_pedido, Decimal('28.93'))  # promedio: 5 a 28.50 y 2 a 30
        d = self.datos()
        del d['stock_inicial']
        d['precio1'] = '30'
        self.client.post(reverse('producto_editar', args=[prod.pk]), d)
        prod.refresh_from_db()
        self.assertEqual(prod.costo_pedido, Decimal('28.93'))

    def test_editar_un_precio_recalcula_el_costo_de_pedido(self):
        prod = self.crear()
        d = self.datos(precio2='25')
        del d['stock_inicial']
        self.client.post(reverse('producto_editar', args=[prod.pk]), d)
        prod.refresh_from_db()
        self.assertEqual(prod.costo_pedido, Decimal('25'))


class DecimalesEnFormulariosTests(Base):
    """Con idioma español, un número con coma no se puede mostrar en <input type=number>: siempre con punto."""

    def test_editar_muestra_los_decimales_con_punto(self):
        prod = self.crear()
        html = self.client.get(reverse('producto_editar', args=[prod.pk])).content.decode()
        self.assertIn('value="45.00"', html)
        self.assertIn('value="28.50"', html)
        self.assertNotIn('value="45,00"', html)

    def test_formulario_nuevo_muestra_el_margen_general_con_punto(self):
        html = self.client.get(reverse('producto_nuevo')).content.decode()
        self.assertIn('name="margen_propio" type="number" placeholder="Margen" autocomplete="off" step="0.01" min="0" value="30', html)
        self.assertNotIn('value="30,', html)


class AlertaYEliminacionTests(Base):
    def setUp(self):
        super().setUp()
        self.prod = self.crear()  # stock 5, mínimo 3
        self.url = reverse('producto_detalle', args=[self.prod.pk])

    def ajustar(self, real):
        return self.client.post(self.url, {'accion': 'ajustar', 'stock_real': real, 'motivo': 'Conteo'})

    def test_un_solo_proveedor_basta(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(codigo='UNO-1', nombre='SSD Uno', proveedor2='', precio2=''))
        self.assertEqual(r.status_code, 302)
        p = Producto.objects.get(codigo='UNO-1')
        self.assertEqual(p.cotizaciones.count(), 1)
        self.assertEqual(p.costo_pedido, Decimal('30'))

    def test_no_se_repite_nombre_con_la_misma_marca(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(codigo='OTRO-9', nombre='ssd 480gb', marca='KINGSTON'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('Ya existe', str(r.context['form'].errors['nombre']))
        self.assertFalse(Producto.objects.filter(codigo='OTRO-9').exists())

    def test_mismo_nombre_con_otra_marca_si_se_permite(self):
        r = self.client.post(reverse('producto_nuevo'), self.datos(codigo='OTRO-8', marca='Samsung'))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Producto.objects.filter(codigo='OTRO-8', marca='Samsung').exists())

    def test_stock_inicial_arranca_en_cero_en_el_formulario(self):
        r = self.client.get(reverse('producto_nuevo'))
        self.assertEqual(r.context['form'].initial['stock_inicial'], 0)

    def test_alerta_solo_al_cruzar_el_minimo(self):
        self.ajustar('3')
        self.assertEqual(Notificacion.objects.filter(usuario=self.admin).count(), 1)
        self.ajustar('2')  # ya estaba bajo: no repite
        self.assertEqual(Notificacion.objects.filter(usuario=self.admin).count(), 1)
        self.ajustar('10')
        self.ajustar('1')  # volvió a cruzar
        self.assertEqual(Notificacion.objects.filter(usuario=self.admin).count(), 2)

    def test_agotado_dice_agotado(self):
        self.ajustar('0')
        self.assertIn('Agotado', Notificacion.objects.get(usuario=self.admin).texto)

    def test_crear_con_stock_bajo_no_avisa(self):
        self.crear(codigo='BAJO-1', stock_inicial='1')
        self.assertFalse(Notificacion.objects.exists())

    def test_descontar_avisa_y_no_deja_negativo(self):
        from . import servicios
        servicios.descontar(self.prod.pk, 2, 'Reparación', self.admin)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 3)
        self.assertEqual(Notificacion.objects.filter(usuario=self.admin).count(), 1)
        m = self.prod.movimientos.first()
        self.assertEqual((m.tipo, m.cantidad, m.stock_resultante), ('SALIDA', 2, 3))
        with self.assertRaises(servicios.StockError):
            servicios.descontar(self.prod.pk, 4, 'Reparación', self.admin)
        with self.assertRaises(servicios.StockError):
            servicios.descontar(self.prod.pk, 0, 'Reparación', self.admin)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 3)

    def test_reposicion_muestra_total_y_lo_guarda_en_el_motivo(self):
        r = self.client.post(self.url, {'accion': 'reponer', 'proveedor': self.p2.pk, 'cantidad': '10', 'precio_unit': '28.50', 'observacion': ''}, follow=True)
        self.assertContains(r, '10 × $28.50 = $285.00')
        self.assertIn('10 × $28.50', self.prod.movimientos.first().motivo)

    def test_detalle_trae_calculadora_y_cantidad_sugerida(self):
        self.ajustar('1')
        r = self.client.get(self.url)
        self.assertContains(r, 'data-reposicion')
        self.assertContains(r, 'data-usar-sugerida="5"')  # 2 × 3 − 1
        self.assertContains(r, 'data-precio="28.50"')

    def test_eliminar_producto_creado_por_error(self):
        r = self.client.post(reverse('producto_eliminar', args=[self.prod.pk]))
        self.assertRedirects(r, reverse('inventario'))
        self.assertFalse(Producto.objects.filter(pk=self.prod.pk).exists())
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_no_se_elimina_con_historial(self):
        self.client.post(self.url, {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '2', 'precio_unit': '30', 'observacion': ''})
        self.client.post(reverse('producto_eliminar', args=[self.prod.pk]))
        self.assertTrue(Producto.objects.filter(pk=self.prod.pk).exists())
        self.assertNotContains(self.client.get(self.url), reverse('producto_eliminar', args=[self.prod.pk]))

    def test_eliminar_solo_por_post_y_solo_admin(self):
        self.assertEqual(self.client.get(reverse('producto_eliminar', args=[self.prod.pk])).status_code, 405)
        self.client.force_login(self.cli)
        self.client.post(reverse('producto_eliminar', args=[self.prod.pk]))
        self.assertTrue(Producto.objects.filter(pk=self.prod.pk).exists())


class NotaDelPedidoTests(Base):
    def test_la_nota_se_ve_en_el_detalle_y_en_el_historial(self):
        prod = self.crear()
        url = reverse('producto_detalle', args=[prod.pk])
        self.client.post(url, {'accion': 'reponer', 'proveedor': self.p1.pk, 'cantidad': '2', 'precio_unit': '30', 'observacion': 'Factura 001-234'})
        self.assertContains(self.client.get(url), 'Factura 001-234')
        r = self.client.get(reverse('movimientos'), {'q': 'Factura 001'})
        self.assertContains(r, 'Factura 001-234')
