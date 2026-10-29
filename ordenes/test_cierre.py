"""Orden cancelada/rechazada, reapertura con descuento, presupuesto adicional, informe de dañados y fechas de entrega."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Notificacion
from inventario.models import Categoria, Producto

from . import presupuestos
from .models import CambioFecha, Orden, Presupuesto, RepuestoDanado, Tarifa
from .test_tecnico import Base


class CierreBase(Base):
    def setUp(self):
        super().setUp()
        cat = Categoria.objects.create(nombre='Pantallas')
        self.pantalla = Producto.objects.create(nombre='Pantalla 15.6', codigo='PAN1', stock=5, precio_venta=Decimal('50'), costo_pedido=Decimal('30'), categoria=cat)
        self.bateria = Producto.objects.create(nombre='Batería HP', codigo='BAT1', stock=5, precio_venta=Decimal('30'), costo_pedido=Decimal('18'), categoria=cat)
        self.mano = Tarifa.objects.create(servicio=None, nombre='Mano de obra', precio=Decimal('20'))
        self.o = self.orden(self.tec, estado='DIAGNOSTICO')

    def enviar(self, repuestos=()):
        return presupuestos.crear(self.o.pk, self.tec, {self.mano.pk: 1}, list(repuestos), '')

    def cancelar(self):
        p = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p.pk, self.cli, False, 'Muy caro')
        self.o.refresh_from_db()
        return p


class RechazoDelClienteTests(CierreBase):
    def test_rechazar_cancela_la_orden_y_guarda_el_motivo(self):
        self.cancelar()
        self.assertEqual((self.o.estado, self.o.estado_previo, self.o.motivo_cierre), ('CANCELADA', 'DIAGNOSTICO', 'Muy caro'))
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='canceló la orden').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='fue rechazada').exists())

    def test_el_tecnico_queda_sin_opciones(self):
        self.cancelar()
        self.client.force_login(self.tec)
        self.client.post(reverse('tecnico_estado', args=[self.o.pk]), {'estado': 'REPARACION'})
        self.client.post(reverse('tecnico_nota', args=[self.o.pk]), {'texto': 'hola'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'CANCELADA')
        self.assertFalse(self.o.notas.exists())
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.crear(self.o.pk, self.tec, {self.mano.pk: 1}, [])
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.solicitar_pieza(self.o.pk, self.tec, 'Batería', 1, '')

    def test_el_tecnico_la_ve_como_rechazada_y_el_cliente_como_cancelada(self):
        self.cancelar()
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Orden rechazada')
        self.assertNotContains(r, 'Cancelada')
        self.assertNotContains(r, 'Avanzar estado')
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Cancelada')
        self.assertContains(r, 'Hablar con la administración')

    def test_el_cliente_habla_con_la_administracion(self):
        from comunicacion.permisos import contacto_para_orden
        self.assertEqual(contacto_para_orden(self.cli, self.o), self.tec)
        self.cancelar()
        self.assertEqual(contacto_para_orden(self.cli, self.o), self.admin)

    def test_rechazar_una_pieza_con_algo_aceptado_no_cancela(self):
        p = self.enviar()
        presupuestos.responder(p.pk, self.cli, True)
        z = presupuestos.solicitar_pieza(self.o.pk, self.tec, 'Teclado', 1, '')
        from inventario.models import Proveedor
        prov = Proveedor.objects.create(nombre='Prov', telefono='0999999999', dias_entrega=2)
        presupuestos.cotizar_pieza(z.pk, self.admin, prov, Decimal('10'), Decimal('15'), 2)
        presupuestos.responder_pieza(z.pk, self.cli, False)
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')

    def test_rechazar_una_pieza_sin_nada_aceptado_cancela(self):
        z = presupuestos.solicitar_pieza(self.o.pk, self.tec, 'Teclado', 1, '')
        from inventario.models import Proveedor
        prov = Proveedor.objects.create(nombre='Prov2', telefono='0999999998', dias_entrega=2)
        presupuestos.cotizar_pieza(z.pk, self.admin, prov, Decimal('10'), Decimal('15'), 2)
        presupuestos.responder_pieza(z.pk, self.cli, False)
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'CANCELADA')

    def test_no_cuenta_como_trabajo_activo(self):
        self.assertIn(self.tec.pk, [u.pk for u in __import__('ordenes.carga', fromlist=['x']).tecnicos_por_carga() if u.activos == 1])
        self.cancelar()
        self.assertEqual([u.activos for u in __import__('ordenes.carga', fromlist=['x']).tecnicos_por_carga() if u.pk == self.tec.pk], [0])


class AdministradorTests(CierreBase):
    def test_rechazar_requiere_motivo(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_rechazar', args=[self.o.pk]), {'motivo': ''})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')
        self.client.post(reverse('orden_rechazar', args=[self.o.pk]), {'motivo': 'La placa no tiene arreglo'})
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.estado_previo), ('RECHAZADA', 'DIAGNOSTICO'))
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='rechazó la orden').exists())

    def test_solo_el_admin_rechaza_reabre_y_termina(self):
        self.cancelar()
        for nombre in ('orden_rechazar', 'orden_reabrir', 'orden_terminar'):
            for u in (self.cli, self.tec):
                self.client.force_login(u)
                self.assertIn(self.client.post(reverse(nombre, args=[self.o.pk]), {}).status_code, (302, 403, 404))
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'CANCELADA')

    def test_reabrir_sin_descuento_vuelve_al_estado_anterior(self):
        self.cancelar()
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {})
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.descuento), ('DIAGNOSTICO', 0))
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='se reabrió').exists())

    def test_reabrir_con_descuento(self):
        p = self.cancelar()  # el cliente rechazó el único presupuesto
        self.assertEqual(self.o.estado, 'CANCELADA')
        self.client.force_login(self.admin)
        # descuento sin motivo: no se aplica
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {'descuento': '10'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'CANCELADA')
        # más que lo presupuestado: no se aplica
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {'descuento': '9999', 'motivo': 'Acuerdo'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'CANCELADA')
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {'sin_mano_obra': 'on', 'motivo': 'Acordado con el cliente'})
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.estado_previo, self.o.motivo_cierre), ('DIAGNOSTICO', '', ''))
        self.assertTrue(self.o.descuento_sin_mano)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE')
        self.assertEqual(p.con_descuento['descuento'], Decimal('20.00'))
        presupuestos.responder(p.pk, self.cli, True)
        f = self.o.factura
        self.assertEqual(f['descuento'], Decimal('20.00'))
        self.assertTrue(any(l['tipo'] == 'Descuento' for l in f['lineas']))
        self.assertEqual(f['total'], f['subtotal'] + f['iva'])
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='se reabrió').exists())

    def test_reabrir_con_descuento_cuando_no_se_acepto_nada(self):
        p = self.cancelar()  # el cliente rechazó el único presupuesto: nada aceptado
        self.assertEqual(self.o.factura['total'], 0)
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {'descuento': '10', 'motivo': 'Acuerdo por el chat'})
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.descuento), ('DIAGNOSTICO', Decimal('10')))
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE')  # ya no sale como rechazado
        presupuestos.responder(p.pk, self.cli, True)
        f = self.o.factura
        self.assertEqual(f['descuento'], Decimal('10.00'))
        self.assertEqual(f['subtotal'], p.subtotal - 10)
        self.assertEqual(f['total'], f['subtotal'] + f['iva'])
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Descuento acordado')

    def test_sin_mano_de_obra_funciona_aunque_no_haya_nada_aceptado(self):
        self.cancelar()
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {'sin_mano_obra': 'on', 'motivo': 'Cortesía'})
        self.o.refresh_from_db()
        self.assertTrue(self.o.descuento_sin_mano)
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')

    def test_cerrado_sin_acuerdo_el_total_es_cero_con_explicacion(self):
        self.cancelar()
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_terminar', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.total_a_pagar), ('LISTO', 0))
        self.assertContains(self.client.get(reverse('orden_admin', args=[self.o.pk])), 'Autoriza la entrega sin cobro')
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'No tienes que pagar nada')

    def test_el_reporte_de_ordenes_muestra_el_descuento(self):
        from datetime import date
        from reportes import datos
        p = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(descuento=Decimal('5'), descuento_motivo='Acuerdo')
        hoy = timezone.localdate()
        rep = datos.ordenes(hoy - timedelta(days=1), hoy + timedelta(days=1))
        self.assertIn('Descuento $', rep.columnas)
        self.assertTrue(any(k == 'Descuentos acordados (sin IVA)' and v == '$5.00' for k, v in rep.resumen))

    def test_cerrar_sin_reparar_deja_la_orden_lista(self):
        self.cancelar()
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_terminar', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')

    def test_el_formulario_no_saca_una_orden_cerrada_ni_la_cierra(self):
        from .forms import OrdenGestionForm
        base = {'tecnico': self.tec.pk, 'prioridad': 'NORMAL', 'fecha_estimada': ''}
        f = OrdenGestionForm({**base, 'estado': 'CANCELADA'}, orden=self.o)
        self.assertFalse(f.is_valid())
        self.cancelar()
        f = OrdenGestionForm({**base, 'estado': 'REPARACION'}, orden=self.o)
        self.assertFalse(f.is_valid())

    def test_el_panel_del_admin_muestra_las_acciones(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[self.o.pk])), 'Rechazar la orden')
        self.cancelar()
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Reabrir y avisar al técnico')
        self.assertContains(r, 'Cerrar sin reparar')


class PresupuestoAdicionalTests(CierreBase):
    def test_se_puede_agregar_otro_presupuesto_despues_de_aceptar_uno(self):
        p1 = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p1.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        p2 = self.enviar([(self.bateria.pk, 1)])
        with self.assertRaises(presupuestos.PresupuestoError):
            self.enviar()  # no dos pendientes a la vez
        presupuestos.responder(p2.pk, self.cli, True)
        self.assertEqual(Presupuesto.objects.filter(orden=self.o, estado='ACEPTADO').count(), 2)
        nombres = [l['detalle'] for l in self.o.factura['lineas']]
        self.assertIn('Pantalla 15.6', nombres)
        self.assertIn('Batería HP', nombres)
        self.bateria.refresh_from_db()
        self.assertEqual(self.bateria.stock, 4)

    def test_la_proforma_de_diagnostico_es_solo_la_primera(self):
        p1 = self.enviar()
        presupuestos.responder(p1.pk, self.cli, True)
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.crear(self.o.pk, self.tec, {self.mano.pk: 1}, [], 'Falta una pieza que hay que esperar', proforma=True)

    def test_el_formulario_aparece_en_reparacion(self):
        p1 = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p1.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        self.client.force_login(self.tec)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Presupuesto adicional')

    def test_rechazar_el_adicional_no_cancela_la_orden(self):
        p1 = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p1.pk, self.cli, True)
        p2 = self.enviar([(self.bateria.pk, 1)])
        presupuestos.responder(p2.pk, self.cli, False)
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')  # ya había aceptado algo: se sigue con eso


class RepuestoDanadoTests(CierreBase):
    def usar(self):
        p = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        item = p.items.get(producto=self.pantalla)
        presupuestos.marcar_en_uso(item.pk, self.tec)

    def test_solo_se_ofrecen_los_repuestos_en_uso(self):
        p = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        self.assertEqual(list(presupuestos.productos_en_uso(self.o)), [])
        self.usar_marcar(p)
        self.assertEqual(list(presupuestos.productos_en_uso(self.o)), [self.pantalla])
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Pantalla 15.6 ·')
        self.assertNotContains(r, '<option value="%d">Batería HP' % self.bateria.pk)

    def usar_marcar(self, p):
        presupuestos.marcar_en_uso(p.items.get(producto=self.pantalla).pk, self.tec)

    def test_no_se_reporta_un_repuesto_que_no_se_usa(self):
        self.usar()
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.reportar_danado(self.o.pk, self.tec, self.bateria.pk, 1, 'Se cayó al piso')
        self.assertFalse(RepuestoDanado.objects.exists())

    def test_se_reporta_aunque_el_stock_este_en_cero(self):
        Producto.objects.filter(pk=self.pantalla.pk).update(stock=1)
        self.usar()  # al aceptar sale la única unidad: stock 0
        self.pantalla.refresh_from_db()
        self.assertEqual(self.pantalla.stock, 0)
        self.assertEqual([p.en_uso for p in presupuestos.productos_en_uso(self.o)], [1])
        presupuestos.reportar_danado(self.o.pk, self.tec, self.pantalla.pk, 1, 'Se rompió al instalarla')
        self.assertEqual(RepuestoDanado.objects.count(), 1)
        self.assertEqual(list(presupuestos.productos_en_uso(self.o)), [])  # ya no hay más por reportar
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.reportar_danado(self.o.pk, self.tec, self.pantalla.pk, 1, 'Otra vez roto')

    def test_no_se_reportan_mas_unidades_de_las_que_se_usan(self):
        self.usar()
        with self.assertRaises(presupuestos.PresupuestoError):
            presupuestos.reportar_danado(self.o.pk, self.tec, self.pantalla.pk, 2, 'Se rompieron')
        self.assertFalse(RepuestoDanado.objects.exists())

    def test_repuesto_extra_se_cobra_y_avisa_al_cliente_pero_el_reemplazo_no(self):
        self.usar()
        antes = self.o.factura['total']
        presupuestos.usar_repuesto(self.o.pk, self.tec, self.bateria.pk, 1)
        self.assertGreater(self.o.factura['total'], antes)
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='Batería HP').exists())
        medio = self.o.factura['total']
        Notificacion.objects.filter(usuario=self.cli).delete()
        presupuestos.usar_repuesto(self.o.pk, self.tec, self.pantalla.pk, 1, reemplazo=True)
        self.assertEqual(self.o.factura['total'], medio)  # el reemplazo no se cobra
        self.assertFalse(Notificacion.objects.filter(usuario=self.cli, texto__contains='Se suma al total').exists())

    def test_el_reporte_no_descuenta_otra_vez_crea_perdida_e_informe(self):
        from finanzas.models import Gasto
        from inventario.models import MovimientoInventario
        self.usar()
        self.pantalla.refresh_from_db()
        antes = self.pantalla.stock
        presupuestos.reportar_danado(self.o.pk, self.tec, self.pantalla.pk, 1, 'Se rompió al instalarla')
        self.pantalla.refresh_from_db()
        self.assertEqual(self.pantalla.stock, antes)  # la unidad ya había salido al aceptarse el presupuesto
        d = RepuestoDanado.objects.get()
        self.assertEqual((d.producto, d.cantidad, d.perdida, d.tecnico, d.motivo), (self.pantalla, 1, Decimal('30.00'), self.tec, 'Se rompió al instalarla'))
        self.assertTrue(Gasto.objects.filter(tipo='PERDIDA', automatico=True, monto=Decimal('30.00')).exists())
        self.assertFalse(MovimientoInventario.objects.filter(producto=self.pantalla, motivo__contains='Dañado').exists())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[self.o.pk])), 'Repuestos dañados en esta orden')


class FechasTests(CierreBase):
    def gestionar(self, **extra):
        datos = {'tecnico': self.tec.pk, 'estado': self.o.estado, 'prioridad': 'NORMAL', 'fecha_estimada': '', 'motivo_fecha': ''}
        datos.update(extra)
        self.client.force_login(self.admin)
        return self.client.post(reverse('orden_admin', args=[self.o.pk]), datos)

    def test_la_primera_fecha_queda_como_prometida_sin_pedir_motivo(self):
        hoy = timezone.localdate()
        self.gestionar(fecha_estimada=(hoy + timedelta(days=3)).isoformat())
        self.o.refresh_from_db()
        self.assertEqual(self.o.fecha_prometida, hoy + timedelta(days=3))
        self.assertFalse(CambioFecha.objects.exists())

    def test_cambiar_una_fecha_ya_dada_exige_motivo_y_lo_guarda(self):
        hoy = timezone.localdate()
        self.o.fecha_estimada = self.o.fecha_prometida = hoy + timedelta(days=3)
        self.o.save()
        nueva = (hoy + timedelta(days=6)).isoformat()
        self.gestionar(fecha_estimada=nueva)
        self.o.refresh_from_db()
        self.assertEqual(self.o.fecha_estimada, hoy + timedelta(days=3))  # sin motivo no cambia
        self.gestionar(fecha_estimada=nueva, motivo_fecha='La pieza llega tarde')
        self.o.refresh_from_db()
        self.assertEqual(self.o.fecha_estimada, hoy + timedelta(days=6))
        self.assertEqual(self.o.fecha_prometida, hoy + timedelta(days=3))  # la promesa original no cambia
        c = CambioFecha.objects.get()
        self.assertEqual((c.fecha_anterior, c.fecha_nueva, c.motivo, c.usuario, c.dias), (hoy + timedelta(days=3), hoy + timedelta(days=6), 'La pieza llega tarde', self.admin, 3))

    def test_pedir_la_pieza_mueve_la_fecha_con_motivo(self):
        from inventario.models import Proveedor
        hoy = timezone.localdate()
        self.o.fecha_estimada = self.o.fecha_prometida = hoy + timedelta(days=1)
        self.o.save()
        p = self.enviar()
        presupuestos.responder(p.pk, self.cli, True)
        z = presupuestos.solicitar_pieza(self.o.pk, self.tec, 'Teclado', 1, '')
        prov = Proveedor.objects.create(nombre='Prov', telefono='0999999999', dias_entrega=4)
        presupuestos.cotizar_pieza(z.pk, self.admin, prov, Decimal('10'), Decimal('15'), 4)
        presupuestos.responder_pieza(z.pk, self.cli, True)
        presupuestos.pedir_pieza(z.pk, self.admin)
        self.o.refresh_from_db()
        c = CambioFecha.objects.get()
        self.assertIn('Teclado', c.motivo)
        self.assertIn('4 días', c.motivo)
        self.assertEqual(self.o.fecha_prometida, hoy + timedelta(days=1))
        self.assertEqual(self.o.fecha_estimada, hoy + timedelta(days=5))

    def test_cumplimiento(self):
        hoy = timezone.localdate()
        self.o.fecha_estimada = hoy + timedelta(days=2)
        self.assertEqual(self.o.cumplimiento, 'En curso')
        self.o.fecha_estimada = hoy - timedelta(days=1)
        self.assertEqual(self.o.cumplimiento, 'Con retraso')
        self.entregar(self.o, estimada=hoy)
        self.assertEqual(self.o.cumplimiento, 'A tiempo')
        self.entregar(self.o, dias_despues_de_estimada=2)
        self.assertEqual(self.o.cumplimiento, 'Con retraso')
        self.o.estado = 'CANCELADA'
        self.assertEqual(self.o.cumplimiento, 'No aplica')

    def test_el_cliente_ve_el_historial_de_fechas(self):
        hoy = timezone.localdate()
        self.o.fecha_estimada = self.o.fecha_prometida = hoy + timedelta(days=3)
        self.o.save()
        CambioFecha.objects.create(orden=self.o, fecha_anterior=hoy + timedelta(days=3), fecha_nueva=hoy + timedelta(days=5), motivo='La pieza llega tarde')
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Primera fecha prometida')
        self.assertContains(r, 'La pieza llega tarde')


class InformesTests(CierreBase):
    def test_informe_de_danados_en_csv(self):
        p = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        presupuestos.marcar_en_uso(p.items.get(producto=self.pantalla).pk, self.tec)
        presupuestos.reportar_danado(self.o.pk, self.tec, self.pantalla.pk, 1, 'Se rompió al instalarla')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('reporte_descargar', args=['danados', 'csv']))
        txt = r.content.decode('utf-8-sig')
        self.assertIn('Se rompió al instalarla', txt)
        self.assertIn('Pantalla 15.6', txt)
        self.assertIn('Tec Uno', txt)
        self.assertIn('30.00', txt)

    def test_informe_de_fechas_y_dashboard(self):
        from core import dashboard
        hoy = timezone.localdate()
        self.entregar(self.o, estimada=hoy)
        self.o.fecha_prometida = hoy - timedelta(days=2)
        self.o.save()
        CambioFecha.objects.create(orden=self.o, fecha_anterior=hoy - timedelta(days=2), fecha_nueva=hoy, motivo='Llegó tarde la pieza')
        self.client.force_login(self.admin)
        txt = self.client.get(reverse('reporte_descargar', args=['fechas', 'csv'])).content.decode('utf-8-sig')
        self.assertIn('Llegó tarde la pieza', txt)
        self.assertIn('A tiempo', txt)
        kpis, graf = dashboard.datos()
        self.assertEqual(graf['cumplimiento']['valores'], [1, 0])

    def test_el_texto_de_finanzas_explica_las_perdidas(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('gastos'))
        self.assertContains(r, 'no se resta otra vez')
        self.assertContains(r, '¿Cómo se calculan estas cifras?')


class DescuentoYCancelacionTests(CierreBase):
    def test_el_descuento_sale_en_la_propuesta_del_cliente(self):
        p = self.enviar([(self.pantalla.pk, 1)])  # 20 + 50 = 70 + IVA
        self.client.force_login(self.admin)
        r = self.client.post(reverse('orden_descuento', args=[self.o.pk]), {'sin_mano_obra': 'on', 'descuento': '10', 'motivo': 'Negociado por el chat'})
        self.assertEqual(r.status_code, 302)
        self.o.refresh_from_db()
        self.assertEqual((self.o.descuento, self.o.descuento_sin_mano), (Decimal('10.00'), True))  # mano de obra 20 + 10 extra
        d = Presupuesto.objects.get(pk=p.pk).con_descuento
        self.assertEqual((d['descuento'], d['subtotal']), (Decimal('30.00'), Decimal('40.00')))
        self.assertEqual(d['total'], d['subtotal'] + d['iva'])
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Descuento acordado')
        self.assertContains(r, 'Negociado por el chat')
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='descuento').exists())
        presupuestos.responder(p.pk, self.cli, True)
        f = self.o.factura
        self.assertEqual(f['subtotal'], Decimal('40.00'))
        self.assertEqual(f['descuento'], Decimal('30.00'))

    def test_descuento_mayor_al_presupuesto_o_sin_motivo_no_se_guarda(self):
        self.enviar([(self.pantalla.pk, 1)])
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_descuento', args=[self.o.pk]), {'descuento': '9999', 'motivo': 'Mucho'})
        self.client.post(reverse('orden_descuento', args=[self.o.pk]), {'descuento': '5'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.descuento, 0)

    def test_rechazar_despues_de_un_descuento_deja_la_orden_rechazada_y_anula_el_descuento(self):
        p = self.cancelar()
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_reabrir', args=[self.o.pk]), {'descuento': '10', 'motivo': 'Última rebaja'})
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE')
        presupuestos.responder(p.pk, self.cli, False, 'Sigue caro')
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.descuento), ('RECHAZADA', 0))
        self.assertEqual(self.o.total_a_pagar, 0)
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='Muchas gracias por confiar').exists())
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'no hay más rebaja')

    def test_el_cliente_cancela_y_paga_la_mano_de_obra(self):
        self.enviar([(self.pantalla.pk, 1)])  # pendiente, nada aceptado
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Cancelar este equipo')
        self.client.post(reverse('equipo_cancelar', args=[self.o.pk]), {'motivo': 'Ya no lo quiero'})
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.cargo_cancelacion), ('CANCELADA', Decimal('20.00')))
        self.assertEqual(self.o.factura['subtotal'], Decimal('20.00'))  # solo la mano de obra
        self.assertTrue(any(l['detalle'] == 'Mano de obra por cancelación' for l in self.o.factura['lineas']))
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='canceló').exists())

    def test_cancelar_con_presupuesto_aceptado_no_cobra_dos_veces_la_mano_de_obra(self):
        p = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p.pk, self.cli, True)
        antes = self.o.factura['total']
        self.client.force_login(self.cli)
        self.client.post(reverse('equipo_cancelar', args=[self.o.pk]))
        self.o.refresh_from_db()
        # la mano de obra no se cobra dos veces; el repuesto que no se usó ya no se cobra
        self.assertEqual((self.o.estado, self.o.cargo_cancelacion, self.o.factura['total']), ('CANCELADA', 0, Decimal('23.00')))
        self.assertGreater(antes, Decimal('23.00'))

    def test_no_se_cancela_si_esta_lista_ni_sin_tecnico_ni_ajena(self):
        Orden.objects.filter(pk=self.o.pk).update(estado='LISTO')
        self.client.force_login(self.cli)
        self.client.post(reverse('equipo_cancelar', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')
        self.client.force_login(self.tec)
        self.assertEqual(self.client.post(reverse('equipo_cancelar', args=[self.o.pk])).status_code, 404)

    def test_el_admin_ve_canceladas_y_rechazadas_en_ordenes_y_dashboard(self):
        self.client.force_login(self.cli)
        self.client.post(reverse('equipo_cancelar', args=[self.o.pk]))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('ordenes') + '?estado=CANCELADA')
        self.assertContains(r, 'Canceladas por el cliente (1)')
        self.assertContains(r, f'#{self.o.pk}')
        from core.dashboard import datos
        kpis, _ = datos()
        self.assertEqual(kpis['canceladas'], 1)

    def test_cerrar_sin_reparar_permite_fijar_la_mano_de_obra(self):
        self.cancelar()
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_terminar', args=[self.o.pk]), {'cargo': '15'})
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.cargo_cancelacion), ('LISTO', Decimal('15.00')))


class AjustesDeFlujoTests(CierreBase):
    def test_aviso_de_una_solicitud_eliminada_no_da_404(self):
        from .models import Servicio
        o = Orden.objects.create(cliente=self.cli, servicio=self.o.servicio, modelo='Laptop X', problema='No enciende del todo')
        Notificacion.avisar(self.admin, f'Nueva solicitud #{o.pk}', reverse('orden_admin', args=[o.pk]))
        n = Notificacion.objects.filter(usuario=self.admin).latest('pk')
        o.delete()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('notificacion_abrir', args=[n.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ya no existe')

    def test_el_admin_corrige_el_servicio_y_se_avisa(self):
        from .models import Servicio
        otro = Servicio.objects.exclude(pk=self.o.servicio_id).first() or Servicio.objects.create(nombre='Otro servicio', descripcion='x', precio_base=Decimal('10'))
        self.client.force_login(self.admin)
        datos = {'servicio': otro.pk, 'tecnico': self.tec.pk, 'estado': self.o.estado, 'prioridad': 'NORMAL', 'fecha_estimada': '', 'motivo_fecha': ''}
        self.client.post(reverse('orden_admin', args=[self.o.pk]), datos)
        self.o.refresh_from_db()
        self.assertEqual(self.o.servicio, otro)
        self.assertTrue(self.o.historial.filter(evento__contains='Servicio corregido').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='se corrigió').exists())

    def test_rechazar_un_pedido_adicional_no_cancela_la_orden(self):
        p1 = self.enviar([(self.pantalla.pk, 1)])
        presupuestos.responder(p1.pk, self.cli, True)
        p2 = self.enviar([(self.bateria.pk, 1)])
        presupuestos.responder(p2.pk, self.cli, False, 'No la necesito')
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')  # sigue abierta
        self.assertEqual(Presupuesto.objects.get(pk=p2.pk).estado, 'RECHAZADO')
        self.assertTrue(all(l['detalle'] != 'Batería HP' for l in self.o.factura['lineas']))
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='no aceptó un pedido').exists())

    def test_pedir_una_pieza_no_exige_haber_aceptado_antes(self):
        z = presupuestos.solicitar_pieza(self.o.pk, self.tec, 'Flex de pantalla', 1, '')
        self.assertEqual(z.nombre_pieza, 'Flex de pantalla')

    def test_descuento_antes_del_presupuesto_se_aplica_al_que_llegue(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_descuento', args=[self.o.pk]), {'sin_mano_obra': 'on', 'descuento': '15', 'motivo': 'Cliente frecuente'})
        self.o.refresh_from_db()
        self.assertEqual((self.o.descuento, self.o.descuento_sin_mano), (Decimal('15.00'), True))
        p = self.enviar([(self.pantalla.pk, 1)])  # mano 20 + pantalla 50
        d = Presupuesto.objects.get(pk=p.pk).con_descuento
        self.assertEqual((d['descuento'], d['subtotal']), (Decimal('35.00'), Decimal('35.00')))
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'sin cobrar la mano de obra')
        presupuestos.responder(p.pk, self.cli, True)
        self.assertEqual(self.o.factura['descuento'], Decimal('35.00'))

    def test_el_dashboard_cuenta_las_rechazadas(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('orden_rechazar', args=[self.o.pk]), {'motivo': 'No tiene arreglo'})
        from core.dashboard import datos
        kpis, graf = datos()
        self.assertEqual(kpis['rechazadas'], 1)
        self.assertIn(1, graf['estados']['valores'])
