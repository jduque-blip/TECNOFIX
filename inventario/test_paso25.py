"""Paso 25: corrección de pedidos sin pérdida, costo promedio al reponer y buscador de repuestos del técnico."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from finanzas.models import Gasto
from inventario import servicios
from inventario.models import Categoria, MovimientoInventario, Pedido, Producto, ProductoProveedor, Proveedor
from usuarios.models import Usuario

CLAVE = 'Clave-segura-123'


class Base(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin Uno', CLAVE)
        self.client.force_login(self.admin)
        self.cat = Categoria.objects.create(nombre='Computadoras')
        self.prov = Proveedor.objects.create(nombre='Compu Mundo')
        self.prod = Producto.objects.create(
            nombre='RAM 8GB', codigo='RAM8', categoria=self.cat, precio_venta=Decimal('40'),
            costo_pedido=Decimal('30'), stock=0, stock_minimo=1,
        )
        ProductoProveedor.objects.create(producto=self.prod, proveedor=self.prov, precio=Decimal('30'))
        self.url = reverse('producto_detalle', args=[self.prod.pk])

    def reponer(self, cantidad, precio):
        return servicios.reponer(self.prod.pk, self.prov, cantidad, Decimal(str(precio)), '', self.admin)[0]


class CostoPromedioTests(Base):
    def test_sin_stock_previo_el_costo_es_el_del_pedido(self):
        self.reponer(4, 30)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.costo_pedido, Decimal('30'))

    def test_proveedor_baja_el_precio_y_lo_viejo_se_promedia(self):
        self.reponer(4, 30)
        self.reponer(4, 20)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 8)
        self.assertEqual(self.prod.costo_pedido, Decimal('25.00'))  # (4*30 + 4*20) / 8

    def test_proveedor_sube_el_precio(self):
        self.reponer(2, 30)
        self.reponer(2, 40)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.costo_pedido, Decimal('35.00'))

    def test_guarda_como_estaba_antes_del_pedido(self):
        self.reponer(3, 30)
        p = self.reponer(3, 24)
        self.assertEqual((p.stock_antes, p.costo_antes), (3, Decimal('30')))

    def test_el_gasto_es_lo_realmente_pagado(self):
        self.reponer(4, 30)
        p = self.reponer(4, 20)
        self.assertEqual(Gasto.objects.get(pedido=p).monto, Decimal('80.00'))

    def test_opcion_actualizar_precio_de_venta(self):
        self.reponer(4, 30)
        servicios.reponer(self.prod.pk, self.prov, 4, Decimal('20'), '', self.admin, actualizar_precio=True)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.precio_venta, self.prod.precio_sugerido)

    def test_sin_la_opcion_el_precio_de_venta_no_cambia(self):
        self.reponer(4, 30)
        self.reponer(4, 20)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.precio_venta, Decimal('40'))

    def test_pantalla_avisa_el_recalculo(self):
        self.reponer(4, 30)
        r = self.client.post(self.url, {'accion': 'reponer', 'proveedor': self.prov.pk, 'cantidad': '4', 'precio_unit': '20', 'observacion': ''}, follow=True)
        textos = ' '.join(str(m) for m in r.context['messages'])
        self.assertIn('Costo promedio recalculado: de $30.00 a $25.00', textos)

    def test_formulario_ofrece_actualizar_precio(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('name="actualizar_precio"', html)
        self.assertIn('¿Cómo funciona?', html)
        self.assertIn('Cambiar también el precio de venta', html)


class CorregirPedidoTests(Base):
    def setUp(self):
        super().setUp()
        self.reponer(4, 30)  # el administrador anotó 4 y eran 2

    def corregir(self, cantidad='2', motivo='Escribí 4 y eran 2'):
        return self.client.post(self.url, {'accion': 'corregir_pedido', 'cantidad_correcta': cantidad, 'motivo': motivo})

    def test_corrige_stock_pedido_y_gasto_sin_perdida(self):
        self.assertRedirects(self.corregir(), self.url, fetch_redirect_response=False)
        self.prod.refresh_from_db()
        p = Pedido.objects.get()
        self.assertEqual((self.prod.stock, p.cantidad, p.cantidad_original), (2, 2, 4))
        self.assertEqual(p.costo_total, Decimal('60.00'))
        gasto = Gasto.objects.get()
        self.assertEqual((gasto.tipo, gasto.monto), (Gasto.Tipo.GASTO, Decimal('60.00')))
        self.assertFalse(Gasto.objects.filter(tipo=Gasto.Tipo.PERDIDA).exists())

    def test_deja_constancia_en_el_kardex(self):
        self.corregir()
        m = self.prod.movimientos.first()
        self.assertEqual((m.tipo, m.cantidad, m.stock_resultante), ('AJUSTE', -2, 2))
        self.assertIn('no es pérdida', m.motivo)
        self.assertIn('Escribí 4 y eran 2', m.motivo)
        self.assertIsNotNone(Pedido.objects.get().corregido_en)

    def test_tambien_se_puede_corregir_hacia_arriba(self):
        self.corregir('6', 'Compré más')
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 6)
        self.assertEqual(Gasto.objects.get().monto, Decimal('180.00'))

    def test_recalcula_el_costo_promedio_del_pedido_corregido(self):
        servicios.corregir_pedido(Pedido.objects.get().pk, 2, 'error', self.admin)  # primer pedido: sin stock previo
        p2 = self.reponer(2, 24)  # 2 a 30 + 2 a 24
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.costo_pedido, Decimal('27.00'))
        servicios.corregir_pedido(p2.pk, 4, 'eran 4', self.admin)  # 2 a 30 + 4 a 24
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.costo_pedido, Decimal('26.00'))

    def test_no_se_puede_si_ya_se_usaron_unidades(self):
        servicios.descontar(self.prod.pk, 3, 'Reparación', self.admin)
        r = self.corregir('2')  # el stock quedaría en 1 - 2 = -1
        self.assertEqual(r.status_code, 200)
        self.assertIn('ya se usaron', str(r.context['f_corregir'].non_field_errors()))
        self.assertEqual(Pedido.objects.get().cantidad, 4)

    def test_solo_el_ultimo_pedido(self):
        primero = Pedido.objects.get()
        self.reponer(1, 30)
        with self.assertRaises(servicios.StockError):
            servicios.corregir_pedido(primero.pk, 2, 'error', self.admin)

    def test_misma_cantidad_o_sin_motivo_se_rechazan(self):
        self.assertEqual(self.corregir('4').status_code, 200)
        self.assertIn('motivo', self.corregir('2', ' ').context['f_corregir'].errors)
        self.assertEqual(Pedido.objects.get().cantidad, 4)

    def test_solo_el_administrador(self):
        tec = Usuario.objects.create_user('t@t.com', 'Tec', CLAVE, rol='TECNICO')
        self.client.force_login(tec)
        self.corregir()
        self.assertEqual(Pedido.objects.get().cantidad, 4)

    def test_la_pantalla_muestra_el_bloque(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('Corregir el último pedido', html)
        self.assertIn('no cuenta como pérdida', html)


class AjusteErrorDeRegistroTests(Base):
    def setUp(self):
        super().setUp()
        self.reponer(5, 30)

    def ajustar(self, real, **extra):
        d = {'accion': 'ajustar', 'stock_real': real, 'motivo': 'Conteo'}
        d.update(extra)
        return self.client.post(self.url, d)

    def test_ajuste_normal_baja_y_cuenta_como_perdida(self):
        self.ajustar('3')
        perdida = Gasto.objects.get(tipo=Gasto.Tipo.PERDIDA)
        self.assertEqual(perdida.monto, Decimal('60.00'))

    def test_error_de_registro_no_cuenta_como_perdida(self):
        self.ajustar('3', error_registro='1')
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 3)
        self.assertFalse(Gasto.objects.filter(tipo=Gasto.Tipo.PERDIDA).exists())
        m = self.prod.movimientos.first()
        self.assertTrue(m.motivo.startswith('Error de registro (no es pérdida)'))

    def test_el_formulario_tiene_la_casilla(self):
        self.assertIn('name="error_registro"', self.client.get(self.url).content.decode())
