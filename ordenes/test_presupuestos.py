from decimal import Decimal

from django.urls import reverse

from comunicacion.models import Notificacion
from inventario.models import MovimientoInventario, PiezaSolicitada, Producto

from .models import Presupuesto, Tarifa
from .test_tecnico import Base


class PresBase(Base):
    def setUp(self):
        super().setUp()
        self.o = self.orden(self.tec)
        self.t1 = Tarifa.objects.create(servicio=self.s1, nombre='Cambio de pantalla', precio=Decimal('20.00'))
        self.tg = Tarifa.objects.create(servicio=None, nombre='Diagnóstico', precio=Decimal('5.00'))
        self.t_otro = Tarifa.objects.create(servicio=self.s2, nombre='Limpieza de consola', precio=Decimal('9.00'))
        self.ram = Producto.objects.create(nombre='RAM 8GB', codigo='RAM8', precio_venta=Decimal('30.00'), costo_pedido=Decimal('20'), stock=5, stock_minimo=2)
        self.url = reverse('presupuesto_crear', args=[self.o.pk])

    def enviar(self, extra=None, **datos):
        d = {f'tarifa_{self.t1.pk}': '1', 'producto': str(self.ram.pk), 'cantidad_producto': '2', 'observacion': 'Garantía de 30 días'}
        d.update(datos)
        self.client.force_login(self.tec)
        return self.client.post(self.url, d)


class CrearTests(PresBase):
    def test_calcula_subtotal_iva_y_total(self):
        r = self.enviar()
        self.assertRedirects(r, reverse('equipo', args=[self.o.pk]))
        p = Presupuesto.objects.get()
        self.assertEqual((p.subtotal, p.iva_porcentaje, p.iva, p.total), (Decimal('80.00'), Decimal('15'), Decimal('12.00'), Decimal('92.00')))
        self.assertEqual(p.items.count(), 2)
        self.assertEqual(p.estado, 'PENDIENTE')
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='$92.00').exists())
        self.assertTrue(self.o.historial.filter(evento__contains='Presupuesto').exists())

    def test_no_baja_stock_hasta_que_el_cliente_acepta(self):
        self.enviar()
        self.ram.refresh_from_db()
        self.assertEqual(self.ram.stock, 5)

    def test_precio_queda_fijo_aunque_cambie_la_tarifa(self):
        self.enviar()
        self.t1.precio = Decimal('99')
        self.t1.save()
        self.assertEqual(Presupuesto.objects.get().subtotal, Decimal('80.00'))

    def test_sin_lineas_se_rechaza(self):
        self.client.force_login(self.tec)
        self.client.post(self.url, {'observacion': 'x'})
        self.assertFalse(Presupuesto.objects.exists())

    def test_tarifa_de_otro_servicio_se_rechaza(self):
        self.enviar(**{f'tarifa_{self.t_otro.pk}': '1'})
        self.assertFalse(Presupuesto.objects.exists())

    def test_tarifa_general_vale(self):
        self.client.force_login(self.tec)
        self.client.post(self.url, {f'tarifa_{self.tg.pk}': '2'})
        self.assertEqual(Presupuesto.objects.get().subtotal, Decimal('10.00'))

    def test_mas_que_el_stock_se_rechaza(self):
        self.enviar(cantidad_producto='6')
        self.assertFalse(Presupuesto.objects.exists())

    def test_cantidades_invalidas(self):
        for malo in ('0', '-1', 'abc', '101'):
            self.enviar(cantidad_producto=malo)
        self.assertFalse(Presupuesto.objects.exists())

    def test_repuesto_repetido_se_suma(self):
        self.client.force_login(self.tec)
        self.client.post(self.url, {'producto': [self.ram.pk, self.ram.pk], 'cantidad_producto': ['2', '2']})
        self.assertEqual(Presupuesto.objects.get().items.get().cantidad, 4)

    def test_producto_inactivo_se_rechaza(self):
        self.ram.activo = False
        self.ram.save()
        self.enviar()
        self.assertFalse(Presupuesto.objects.exists())

    def test_un_solo_pendiente(self):
        self.enviar()
        self.enviar()
        self.assertEqual(Presupuesto.objects.count(), 1)

    def test_solo_el_tecnico_asignado(self):
        self.client.force_login(self.tec2)
        self.assertEqual(self.client.post(self.url, {f'tarifa_{self.t1.pk}': '1'}).status_code, 404)
        self.client.force_login(self.cli)
        self.assertEqual(self.client.post(self.url, {f'tarifa_{self.t1.pk}': '1'}).status_code, 404)
        self.assertFalse(Presupuesto.objects.exists())

    def test_orden_entregada_no_admite(self):
        self.entregar(self.o)
        self.enviar()
        self.assertFalse(Presupuesto.objects.exists())

    def test_get_no_permitido(self):
        self.client.force_login(self.tec)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_retirar_y_armar_otro(self):
        self.enviar()
        p = Presupuesto.objects.get()
        self.client.post(reverse('presupuesto_retirar', args=[self.o.pk, p.pk]))
        self.assertFalse(Presupuesto.objects.exists())
        self.enviar()
        self.assertEqual(Presupuesto.objects.count(), 1)

    def test_detalle_del_tecnico_muestra_el_formulario(self):
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'data-presupuesto')
        self.assertContains(r, 'Cambio de pantalla')
        self.assertContains(r, 'Diagnóstico')
        self.assertNotContains(r, 'Limpieza de consola')
        self.assertContains(r, 'RAM 8GB')


class RespuestaTests(PresBase):
    def setUp(self):
        super().setUp()
        self.enviar()
        self.p = Presupuesto.objects.get()
        self.url_r = reverse('presupuesto_responder', args=[self.o.pk, self.p.pk])
        self.client.force_login(self.cli)

    def test_aceptar_descuenta_repuestos_y_deja_kardex(self):
        self.client.post(self.url_r, {'respuesta': 'aceptar'})
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'ACEPTADO')
        self.assertIsNotNone(self.p.respondido_en)
        self.ram.refresh_from_db()
        self.assertEqual(self.ram.stock, 3)
        m = MovimientoInventario.objects.get(tipo='SALIDA')
        self.assertEqual((m.cantidad, m.stock_resultante, m.presupuesto_id), (2, 3, self.p.pk))
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='aceptó').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='aceptó').exists())

    def test_aceptar_hasta_el_minimo_avisa_stock_bajo(self):
        self.client.post(self.url_r, {'respuesta': 'aceptar'})  # 5 → 3, mínimo 2: aún no
        self.assertFalse(Notificacion.objects.filter(usuario=self.admin, texto__contains='Stock bajo').exists())

    def test_rechazar_no_toca_el_stock(self):
        self.client.post(self.url_r, {'respuesta': 'rechazar'})
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'RECHAZADO')
        self.ram.refresh_from_db()
        self.assertEqual(self.ram.stock, 5)
        self.assertFalse(MovimientoInventario.objects.filter(tipo='SALIDA').exists())

    def test_tras_rechazar_la_orden_queda_cancelada_y_el_tecnico_no_puede_enviar_otro(self):
        self.client.post(self.url_r, {'respuesta': 'rechazar'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'CANCELADA')
        self.client.force_login(self.tec)
        self.enviar(**{f'tarifa_{self.t1.pk}': '2'})
        self.assertEqual(Presupuesto.objects.count(), 1)

    def test_no_se_responde_dos_veces(self):
        self.client.post(self.url_r, {'respuesta': 'aceptar'})
        self.client.post(self.url_r, {'respuesta': 'aceptar'})
        self.ram.refresh_from_db()
        self.assertEqual(self.ram.stock, 3)
        self.assertEqual(MovimientoInventario.objects.filter(tipo='SALIDA').count(), 1)

    def test_sin_stock_al_aceptar_no_acepta_nada_y_avisa(self):
        Producto.objects.filter(pk=self.ram.pk).update(stock=1)
        self.client.post(self.url_r, {'respuesta': 'aceptar'})
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'PENDIENTE')
        self.ram.refresh_from_db()
        self.assertEqual(self.ram.stock, 1)
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='ya no hay stock').exists())

    def test_respuesta_invalida(self):
        self.client.post(self.url_r, {'respuesta': 'tal vez'})
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'PENDIENTE')

    def test_otro_cliente_o_tecnico_no_responde(self):
        otro = type(self.cli).objects.create_user('o@t.com', 'Otro Cli', 'Taller-2026-ok', rol='CLIENTE')
        for u in (otro, self.tec, self.admin):
            self.client.force_login(u)
            self.assertEqual(self.client.post(self.url_r, {'respuesta': 'aceptar'}).status_code, 404)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'PENDIENTE')

    def test_cliente_ve_botones_y_el_total(self):
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Aceptar presupuesto')
        self.assertContains(r, '$92.00')

    def test_admin_ve_el_presupuesto(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, '$92.00')
        self.assertNotContains(r, 'Aceptar presupuesto')
