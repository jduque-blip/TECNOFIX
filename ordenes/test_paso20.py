"""Paso 20: cobro real al cancelar o rechazar, «Cancelada» que prevalece, página de eliminado, proforma con piezas, técnico desactivado e IVA por orden."""

from decimal import Decimal

from django.urls import reverse

from comunicacion.models import Notificacion
from core.models import Configuracion
from inventario.models import Categoria, PiezaSolicitada, Producto

from . import presupuestos
from .models import Orden, Presupuesto, Tarifa
from .test_tecnico import Base


class Paso20Base(Base):
    def setUp(self):
        super().setUp()
        self.diag = Tarifa.objects.create(servicio=self.s1, nombre='Diagnóstico', precio=Decimal('5.00'))
        self.mano = Tarifa.objects.create(servicio=None, nombre='Mano de obra', precio=Decimal('20.00'))
        cat = Categoria.objects.create(nombre='Pantallas')
        self.pantalla = Producto.objects.create(nombre='Pantalla 15.6', codigo='PAN1', stock=5, precio_venta=Decimal('50'), costo_pedido=Decimal('30'), categoria=cat)


class CobroTests(Paso20Base):
    def test_cancelar_en_diagnostico_cobra_solo_el_diagnostico(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        presupuestos.crear(o.pk, self.tec, {self.mano.pk: 1}, [], '')
        self.assertEqual(presupuestos.cargo_cancelacion(o), Decimal('5.00'))
        self.assertEqual(presupuestos.cancelar_por_cliente(o.pk, self.cli, 'No quiero'), Decimal('5.00'))
        o.refresh_from_db()
        self.assertEqual((o.estado, o.cargo_cancelacion, o.resultado), ('CANCELADA', Decimal('5.00'), 'CANCELADA'))

    def test_cancelar_en_reparacion_cobra_la_mano_de_obra(self):
        o = self.orden(self.tec, estado='REPARACION')
        presupuestos.crear(o.pk, self.tec, {self.diag.pk: 1, self.mano.pk: 1}, [], '')
        self.assertEqual(presupuestos.cargo_cancelacion(o), Decimal('25.00'))

    def test_el_admin_cierra_sin_cobrar_y_sigue_figurando_cancelada(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        presupuestos.cancelar_por_cliente(o.pk, self.cli)
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_terminar', args=[o.pk]), {'cobro': 'nada'})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.cargo_cancelacion, o.resultado), ('LISTO', 0, 'CANCELADA'))
        self.client.post(reverse('entrega_sin_cobro', args=[o.pk]))
        self.client.post(reverse('entregar', args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual(o.estado, 'ENTREGADO')
        self.assertIn('Cancelada', o.nombre_estado(self.cli))
        self.assertIn('equipo entregado', o.nombre_estado(self.cli))
        self.assertIn('Rechazada', o.nombre_estado(self.tec))   # al técnico siempre le aparece rechazada
        r = self.client.get(reverse('ordenes') + '?estado=CANCELADA')
        self.assertContains(r, f'#{o.pk}')
        r = self.client.get(reverse('ordenes') + '?estado=ENTREGADO')
        self.assertNotContains(r, f'#{o.pk}')
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[o.pk])), 'Cancelada')

    def test_rechazar_con_cobro_de_diagnostico(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_rechazar', args=[o.pk]), {'motivo': 'No tiene arreglo', 'cobro': 'diagnostico'})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.cargo_cancelacion), ('RECHAZADA', Decimal('5.00')))

    def test_rechazar_por_defecto_no_cobra_y_otro_valor_se_valida(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_rechazar', args=[o.pk]), {'motivo': 'No tiene arreglo', 'cobro': 'otro', 'cargo': 'abc'})
        o.refresh_from_db()
        self.assertEqual(o.estado, 'DIAGNOSTICO')
        self.client.post(reverse('orden_rechazar', args=[o.pk]), {'motivo': 'No tiene arreglo'})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.cargo_cancelacion), ('RECHAZADA', 0))

    def test_reabrir_quita_el_resultado(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        presupuestos.cancelar_por_cliente(o.pk, self.cli)
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_reabrir', args=[o.pk]), {})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.resultado), ('DIAGNOSTICO', ''))


class EliminadoTests(Paso20Base):
    def test_equipo_eliminado_no_muestra_la_pagina_fria(self):
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[99999]))
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'fue eliminado', status_code=404)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[99999])), 'fue eliminado', status_code=404)
        self.assertContains(self.client.get('/una/ruta/que/no/existe/'), 'No encontramos', status_code=404)


class PiezasTests(Paso20Base):
    def test_proforma_con_pieza_en_stock_y_varias_que_faltan(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.client.force_login(self.tec)
        self.client.post(reverse('presupuesto_crear', args=[o.pk]), {
            f'tarifa_{self.mano.pk}': '1', 'producto': str(self.pantalla.pk), 'cantidad_producto': '1', 'observacion': 'Hay que esperar la batería y el cargador',
            'proforma': 'on', 'falta_nombre': ['Batería HP', 'Cargador 65W', ''], 'falta_cantidad': ['1', '2', '1'],
        })
        p = Presupuesto.objects.get()
        self.assertTrue(p.espera_pieza)
        self.assertEqual(p.items.filter(tipo='REPUESTO').count(), 1)
        self.assertEqual(PiezaSolicitada.objects.filter(orden=o).count(), 2)
        o.refresh_from_db()
        self.assertTrue(o.espera_repuesto)

    def test_descartar_pieza_avisando_al_cliente_y_rechazando_sin_cobro(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        z = presupuestos.solicitar_pieza(o.pk, self.tec, 'Pantalla rara', 1, '')
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_descartar', args=[o.pk, z.pk]), {'nota_admin': 'No se consigue', 'avisar_cliente': 'on', 'cerrar_orden': 'on', 'cobro': 'nada'})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.cargo_cancelacion), ('RECHAZADA', 0))
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='No fue posible conseguir').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='No se pedirá').exists())

    def test_descartar_sin_cerrar_solo_avisa(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        z = presupuestos.solicitar_pieza(o.pk, self.tec, 'Pantalla rara', 1, '')
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_descartar', args=[o.pk, z.pk]), {'nota_admin': ''})
        o.refresh_from_db()
        self.assertEqual(o.estado, 'DIAGNOSTICO')
        self.assertFalse(Notificacion.objects.filter(usuario=self.cli, texto__contains='No fue posible conseguir').exists())


class TecnicoDesactivadoTests(Paso20Base):
    def test_al_desactivar_sus_ordenes_quedan_sin_tecnico_y_se_avisa(self):
        a = self.orden(self.tec, estado='REPARACION')
        b = self.orden(self.tec, estado='DIAGNOSTICO')
        c = self.orden(self.tec)
        self.entregar(c)
        self.client.force_login(self.admin)
        self.client.post(reverse('usuario_estado', args=[self.tec.pk]))
        for o in (a, b, c):
            o.refresh_from_db()
        self.assertIsNone(a.tecnico)
        self.assertIsNone(b.tecnico)
        self.assertEqual(c.tecnico, self.tec)
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='quedó sin técnico').exists())


class IvaPorOrdenTests(Paso20Base):
    def test_un_cambio_de_iva_no_afecta_a_las_ordenes_existentes(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.assertEqual(o.iva_porcentaje, Decimal('15'))
        Configuracion.objects.filter(clave=Configuracion.IVA).update(valor=Decimal('12'))
        p = presupuestos.crear(o.pk, self.tec, {self.mano.pk: 1}, [], '')
        self.assertEqual((p.iva_porcentaje, p.iva), (Decimal('15'), Decimal('3.00')))
        nueva = self.orden(self.tec, estado='DIAGNOSTICO')
        self.assertEqual(nueva.iva_porcentaje, Decimal('12'))
        p2 = presupuestos.crear(nueva.pk, self.tec, {self.mano.pk: 1}, [], '')
        self.assertEqual(p2.iva, Decimal('2.40'))


class AgotadaTests(Paso20Base):
    def setUp(self):
        super().setUp()
        self.agotado = Producto.objects.create(nombre='Batería HP', codigo='BAT9', stock=0, precio_venta=Decimal('40'), costo_pedido=Decimal('25'))
        self.o = self.orden(self.tec, estado='DIAGNOSTICO')

    def test_no_se_marca_agotada_una_pieza_con_stock(self):
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.solicitar_pieza(self.o.pk, self.tec, '', 1, '', self.pantalla, None, agotada=True)

    def test_flujo_completo_de_pieza_agotada(self):
        from inventario import servicios as stock
        from inventario.models import Proveedor
        z = presupuestos.solicitar_pieza(self.o.pk, self.tec, '', 1, '', self.agotado, None, agotada=True)
        self.assertTrue(z.agotada)
        self.assertEqual(z.precio, Decimal('40'))
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='AGOTÓ').exists())
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Esta pieza se agotó en el inventario')
        self.client.post(reverse('pieza_reponer', args=[self.o.pk, z.pk]), {'dias_reposicion': '3'})
        z.refresh_from_db()
        self.assertEqual((z.estado, z.dias_entrega), ('PEDIDA', 3))
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='se repondrá en 3 días').exists())
        self.o.refresh_from_db()
        self.assertIsNotNone(self.o.fecha_estimada)
        prov = Proveedor.objects.create(nombre='Compu Mundo', dias_entrega=3)
        stock.reponer(self.agotado.pk, prov, 5, Decimal('25'), '', self.admin)
        z.refresh_from_db()
        self.o.refresh_from_db()
        self.assertEqual(z.estado, 'DISPONIBLE')
        self.assertEqual(self.o.estado, 'REPARACION')
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Llegó la pieza').exists())
        self.assertTrue(any(l['tipo'] == 'Pieza' for l in self.o.factura['lineas']))

    def test_cotizar_con_categoria_actualiza_la_pieza(self):
        from inventario.models import Proveedor
        z = presupuestos.solicitar_pieza(self.o.pk, self.tec, 'Pantalla rara', 1, '')
        cat = Categoria.objects.get(nombre='Pantallas')
        prov = Proveedor.objects.create(nombre='Pantallas SA', dias_entrega=2)
        prov.categorias.add(cat)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, f'data-cats="{cat.pk}"')
        self.client.post(reverse('pieza_cotizar', args=[self.o.pk, z.pk]), {'categoria': str(cat.pk), 'proveedor': str(prov.pk), 'costo': '30', 'precio': '45'})
        z.refresh_from_db()
        self.assertEqual((z.categoria, z.estado), (cat, 'COTIZADA'))


class PanelClienteTests(Paso20Base):
    def test_rechazada_y_entregada_sigue_apareciendo_como_rechazada(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_rechazar', args=[o.pk]), {'motivo': 'No tiene arreglo', 'cobro': 'nada'})
        self.client.post(reverse('orden_terminar', args=[o.pk]), {'cobro': 'nada'})
        self.client.post(reverse('entrega_sin_cobro', args=[o.pk]))
        self.client.post(reverse('entregar', args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual(o.estado, 'ENTREGADO')
        self.client.force_login(self.cli)
        r = self.client.get(reverse('panel'))
        self.assertContains(r, 'Rechazada · equipo entregado')
        r = self.client.get(reverse('panel') + '?estado=RECHAZADA')
        self.assertContains(r, f'#{o.pk}')


class CancelarConRepuestosTests(Paso20Base):
    def test_el_admin_ve_lo_usado_y_decide_que_cobrar(self):
        o = self.orden(self.tec, estado='REPARACION')
        presupuestos.crear(o.pk, self.tec, {self.mano.pk: 1}, [], '')
        presupuestos.usar_repuesto(o.pk, self.tec, self.pantalla.pk, 1)
        presupuestos.cancelar_por_cliente(o.pk, self.cli, '')
        o.refresh_from_db()
        self.assertIn('Repuestos ya usados: 1 × Pantalla 15.6', o.motivo_cierre)
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='Repuestos ya usados').exists())
        self.assertEqual(o.cargo_cancelacion, Decimal('20.00'))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[o.pk])), 'Repuestos que el técnico ya usó')
        self.client.post(reverse('orden_terminar', args=[o.pk]), {'cobro': 'mantener', 'usos_listados': '1'})
        o.refresh_from_db()
        self.assertEqual(o.factura['subtotal'], Decimal('20.00'))   # el repuesto desmarcado no se cobra


class CierreConPresupuestoAceptadoTests(Paso20Base):
    def test_solo_se_cobra_el_repuesto_usado_y_el_otro_vuelve_al_inventario(self):
        cat = self.pantalla.categoria
        bateria = Producto.objects.create(nombre='Batería', codigo='B1', stock=5, precio_venta=Decimal('30'), costo_pedido=Decimal('18'), categoria=cat)
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        p = presupuestos.crear(o.pk, self.tec, {self.mano.pk: 1}, [(self.pantalla.pk, 1), (bateria.pk, 1)], '')
        presupuestos.responder(p.pk, self.cli, True)
        o.refresh_from_db()
        o.estado = 'REPARACION'
        o.save()
        item = p.items.get(producto=self.pantalla)
        presupuestos.marcar_en_uso(item.pk, self.tec)
        self.assertEqual(o.factura['subtotal'], Decimal('100.00'))      # todo el presupuesto: 20 + 50 + 30
        presupuestos.cancelar_por_cliente(o.pk, self.cli)
        o.refresh_from_db()
        self.assertEqual(o.factura['subtotal'], Decimal('70.00'))       # mano de obra 20 + pantalla usada 50
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[o.pk])), 'Repuestos del presupuesto aceptado')
        self.client.post(reverse('orden_terminar', args=[o.pk]), {'cobro': 'mantener'})
        bateria.refresh_from_db()
        self.pantalla.refresh_from_db()
        self.assertEqual(bateria.stock, 5)        # volvió al inventario
        self.assertEqual(self.pantalla.stock, 4)  # la usada no vuelve


class ServiciosDesplegablesTests(Paso20Base):
    def test_servicios_por_lista_desplegable_se_suman_si_se_repiten(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[o.pk]))
        self.assertContains(r, 'data-plantilla-servicio')
        self.client.post(reverse('presupuesto_crear', args=[o.pk]), {
            'servicio_sel': [str(self.diag.pk), str(self.mano.pk), str(self.mano.pk), ''], 'servicio_cant': ['1', '1', '1', '1'],
        })
        p = Presupuesto.objects.get()
        self.assertEqual(p.subtotal, Decimal('45.00'))   # 5 + 2 × 20
