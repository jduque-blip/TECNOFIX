from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Notificacion
from inventario.models import PiezaSolicitada, Proveedor

from . import carga, presupuestos
from .models import Presupuesto, SolicitudEstado, Tarifa
from .test_tecnico import Base


class CargaTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_ordena_del_mas_libre_al_mas_ocupado(self):
        self.orden(self.tec, estado='DIAGNOSTICO')
        self.orden(self.tec, estado='REPARACION')
        self.orden(self.tec2, estado='DIAGNOSTICO')
        nombres = [u.nombre for u in carga.tecnicos_por_carga()]
        self.assertEqual(nombres, ['Tec Dos', 'Tec Uno'])
        u = {u.nombre: u for u in carga.tecnicos_por_carga()}['Tec Uno']
        self.assertEqual((u.activos, u.n_diagnostico, u.n_reparacion), (2, 1, 1))
        self.assertEqual(carga.detalle(u), '2 casos activos: 1 en diagnóstico, 1 en reparación')

    def test_listos_y_entregados_no_ocupan_al_tecnico(self):
        self.orden(self.tec, estado='LISTO')
        o = self.orden(self.tec)
        self.entregar(o)
        u = {u.nombre: u for u in carga.tecnicos_por_carga()}['Tec Uno']
        self.assertEqual(u.activos, 0)
        self.assertIn('1 listo para entrega', carga.detalle(u))

    def test_libre_se_muestra_libre(self):
        u = {u.nombre: u for u in carga.tecnicos_por_carga()}['Tec Uno']
        self.assertEqual(carga.detalle(u), 'libre')

    def test_empate_gana_quien_lleva_mas_tiempo_sin_recibir(self):
        ahora = timezone.now()
        self.orden(self.tec, estado='LISTO', asignada_en=ahora)
        self.orden(self.tec2, estado='LISTO', asignada_en=ahora - timedelta(days=2))
        self.assertEqual([u.nombre for u in carga.tecnicos_por_carga()][0], 'Tec Dos')

    def test_la_pantalla_muestra_la_carga_y_recomienda(self):
        self.orden(self.tec, estado='REPARACION')
        o = self.orden()
        r = self.client.get(reverse('orden_admin', args=[o.pk]))
        self.assertContains(r, 'Carga de los técnicos')
        self.assertContains(r, '1 caso activo: 1 en reparación')
        self.assertContains(r, '★ más libre')
        self.assertEqual(r.context['recomendado'], self.tec2.pk)

    def test_asignar_pasa_de_recibido_a_diagnostico(self):
        o = self.orden()
        self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': self.tec.pk, 'estado': 'RECIBIDO', 'prioridad': 'NORMAL'})
        o.refresh_from_db()
        self.assertEqual((o.tecnico, o.estado), (self.tec, 'DIAGNOSTICO'))
        self.assertIsNotNone(o.asignada_en)
        self.assertTrue(o.historial.filter(evento__contains='diagnóstico').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='En diagnóstico').exists())

    def test_quitar_tecnico_limpia_la_asignacion(self):
        o = self.orden()
        self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': self.tec.pk, 'estado': 'RECIBIDO', 'prioridad': 'NORMAL'})
        self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': '', 'estado': 'RECIBIDO', 'prioridad': 'NORMAL'})
        o.refresh_from_db()
        self.assertIsNone(o.asignada_en)


class EstadosTecnicoTests(Base):
    def setUp(self):
        super().setUp()
        self.o = self.orden(self.tec, estado='REPARACION')
        self.client.force_login(self.tec)

    def cambiar(self, estado):
        return self.client.post(reverse('tecnico_estado', args=[self.o.pk]), {'estado': estado})

    def test_solo_avanza(self):
        self.cambiar('DIAGNOSTICO')
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'REPARACION')
        self.cambiar('LISTO')
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')

    def test_no_vuelve_atras_ni_a_recibido(self):
        self.cambiar('RECIBIDO')
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'REPARACION')

    def test_desde_listo_no_hay_mas_estados(self):
        self.cambiar('LISTO')
        self.cambiar('REPARACION')
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Ya no hay más estados')

    def test_el_selector_solo_ofrece_los_siguientes(self):
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertEqual([v for v, _ in r.context['form_estado'].fields['estado'].choices], ['LISTO'])

    def test_pedir_retroceso(self):
        r = self.client.post(reverse('tecnico_retroceso', args=[self.o.pk]), {'estado': 'DIAGNOSTICO', 'motivo': 'Marqué reparación sin querer'})
        self.assertRedirects(r, reverse('equipo', args=[self.o.pk]))
        sol = SolicitudEstado.objects.get()
        self.assertEqual((sol.estado, sol.estado_actual, sol.estado_pedido), ('PENDIENTE', 'REPARACION', 'DIAGNOSTICO'))
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'REPARACION')  # sigue igual hasta que el admin responda
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='pide volver').exists())

    def test_retroceso_validaciones(self):
        url = reverse('tecnico_retroceso', args=[self.o.pk])
        self.client.post(url, {'estado': 'LISTO', 'motivo': 'Quiero avanzar por aquí'})   # no es retroceso
        self.client.post(url, {'estado': 'DIAGNOSTICO', 'motivo': ' '})                   # sin motivo
        self.client.post(url, {'estado': 'ENTREGADO', 'motivo': 'Quiero entregar ya'})    # inválido
        self.assertFalse(SolicitudEstado.objects.exists())
        self.client.post(url, {'estado': 'DIAGNOSTICO', 'motivo': 'Primera solicitud'})
        self.client.post(url, {'estado': 'RECIBIDO', 'motivo': 'Segunda solicitud'})      # ya hay una pendiente
        self.assertEqual(SolicitudEstado.objects.count(), 1)

    def test_otro_tecnico_o_cliente_no_piden(self):
        for u in (self.tec2, self.cli, self.admin):
            self.client.force_login(u)
            r = self.client.post(reverse('tecnico_retroceso', args=[self.o.pk]), {'estado': 'DIAGNOSTICO', 'motivo': 'Motivo cualquiera'})
            self.assertEqual(r.status_code, 404)

    def test_admin_aprueba_y_la_orden_retrocede(self):
        self.client.post(reverse('tecnico_retroceso', args=[self.o.pk]), {'estado': 'DIAGNOSTICO', 'motivo': 'Error de estado'})
        sol = SolicitudEstado.objects.get()
        self.client.force_login(self.admin)
        self.client.post(reverse('solicitud_responder', args=[self.o.pk, sol.pk]), {'accion': 'aprobar'})
        self.o.refresh_from_db()
        sol.refresh_from_db()
        self.assertEqual((self.o.estado, sol.estado), ('DIAGNOSTICO', 'APROBADA'))
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Se aprobó').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='En diagnóstico').exists())

    def test_admin_rechaza_con_motivo(self):
        self.client.post(reverse('tecnico_retroceso', args=[self.o.pk]), {'estado': 'DIAGNOSTICO', 'motivo': 'Error de estado'})
        sol = SolicitudEstado.objects.get()
        self.client.force_login(self.admin)
        self.client.post(reverse('solicitud_responder', args=[self.o.pk, sol.pk]), {'accion': 'rechazar', 'respuesta': 'Ya se cambió la pieza'})
        self.o.refresh_from_db()
        sol.refresh_from_db()
        self.assertEqual((self.o.estado, sol.estado, sol.respuesta), ('REPARACION', 'RECHAZADA', 'Ya se cambió la pieza'))
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Ya se cambió la pieza').exists())

    def test_no_se_responde_dos_veces_y_solo_admin(self):
        self.client.post(reverse('tecnico_retroceso', args=[self.o.pk]), {'estado': 'DIAGNOSTICO', 'motivo': 'Error de estado'})
        sol = SolicitudEstado.objects.get()
        url = reverse('solicitud_responder', args=[self.o.pk, sol.pk])
        r = self.client.post(url, {'accion': 'aprobar'})   # el técnico no puede
        self.assertNotEqual(r.status_code, 200)
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'PENDIENTE')
        self.client.force_login(self.admin)
        self.client.post(url, {'accion': 'aprobar'})
        self.o.refresh_from_db()
        self.o.estado = 'LISTO'
        self.o.save()
        self.client.post(url, {'accion': 'aprobar'})  # ya respondida
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')

    def test_el_admin_ve_la_solicitud_pendiente(self):
        self.client.post(reverse('tecnico_retroceso', args=[self.o.pk]), {'estado': 'DIAGNOSTICO', 'motivo': 'Error de estado'})
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'El técnico pide cambiar el estado')
        self.assertContains(r, 'Aprobar y cambiar estado')


class ProformaYPiezaTests(Base):
    def setUp(self):
        super().setUp()
        self.o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.diag = Tarifa.objects.create(servicio=self.s1, nombre='Diagnóstico', precio=Decimal('5.00'))
        self.mano = Tarifa.objects.create(servicio=None, nombre='Mano de obra', precio=Decimal('20.00'))
        self.prov = Proveedor.objects.create(nombre='Compu Mundo', dias_entrega=3)

    def proforma(self, nota='Falta la pantalla; hay que esperar la pieza.', **extra):
        d = {f'tarifa_{self.diag.pk}': '1', f'tarifa_{self.mano.pk}': '1', 'observacion': nota, 'proforma': 'on'}
        d.update(extra)
        self.client.force_login(self.tec)
        return self.client.post(reverse('presupuesto_crear', args=[self.o.pk]), d)

    def registrar_por_formulario(self, z, codigo='PAN-156'):
        """El administrador registra la pieza en el inventario desde «Nuevo producto»: eso es la llegada."""
        from inventario.models import Producto
        self.client.force_login(self.admin)
        r = self.client.post(reverse('producto_nuevo'), {
            'pieza': z.pk, 'nombre': z.nombre_pieza, 'codigo': codigo, 'marca': 'Genérica', 'categoria': '', 'precio_venta': '55',
            'costo_pedido': '', 'stock_minimo': '1', 'margen_propio': '',
            'proveedor1': self.prov.pk, 'precio1': '40', 'proveedor2': '', 'precio2': '', 'activo': 'on',
        })
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        return Producto.objects.get(codigo=codigo)

    def registrar_producto(self, z, stock=0):
        from inventario.models import Producto
        prod = Producto.objects.create(nombre=z.nombre_pieza, codigo='PAN-156', precio_venta=Decimal('55'), costo_pedido=Decimal('40'), stock=stock, stock_minimo=1)
        PiezaSolicitada.objects.filter(pk=z.pk).update(producto=prod)
        z.refresh_from_db()
        return prod

    def aceptar_proforma(self):
        self.proforma()
        p = Presupuesto.objects.get()
        self.client.force_login(self.cli)
        self.client.post(reverse('presupuesto_responder', args=[self.o.pk, p.pk]), {'respuesta': 'aceptar'})
        return p

    def pedir(self, nombre='Pantalla 15.6 LED', cantidad='1'):
        self.client.force_login(self.tec)
        return self.client.post(reverse('pieza_pedir', args=[self.o.pk]), {'nombre_pieza': nombre, 'cantidad_pieza': cantidad, 'observacion_pieza': 'HP 15'})

    def test_proforma_solo_diagnostico_y_mano_de_obra(self):
        self.proforma()
        p = Presupuesto.objects.get()
        self.assertTrue(p.espera_pieza)
        self.assertEqual((p.subtotal, p.iva, p.total), (Decimal('25.00'), Decimal('3.75'), Decimal('28.75')))

    def test_proforma_exige_nota(self):
        self.proforma(nota='corta')
        self.assertFalse(Presupuesto.objects.exists())

    def test_el_cliente_ve_la_explicacion(self):
        self.proforma()
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Proforma de diagnóstico')
        self.assertContains(r, 'ya quedan cobrados y no se repiten')

    def test_se_pide_pieza_sin_esperar_a_que_acepten_nada(self):
        self.pedir()
        self.assertEqual(PiezaSolicitada.objects.count(), 1)

    def test_pedir_pieza_marca_espera_y_avisa(self):
        self.aceptar_proforma()
        self.pedir()
        z = PiezaSolicitada.objects.get()
        self.assertEqual((z.estado, z.cantidad, z.tecnico), ('PENDIENTE', 1, self.tec))
        self.o.refresh_from_db()
        self.assertTrue(self.o.espera_repuesto)
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='Pantalla 15.6 LED').exists())

    def test_validaciones_del_pedido(self):
        self.aceptar_proforma()
        self.pedir(nombre='  ')
        self.pedir(cantidad='0')
        self.pedir(cantidad='x')
        self.assertFalse(PiezaSolicitada.objects.exists())

    def cotizar(self, **extra):
        z = PiezaSolicitada.objects.get()
        d = {'proveedor': self.prov.pk, 'costo': '40', 'precio': '55', 'dias_entrega': '', 'nota_admin': ''}
        d.update(extra)
        self.client.force_login(self.admin)
        r = self.client.post(reverse('pieza_cotizar', args=[self.o.pk, z.pk]), d)
        z.refresh_from_db()
        return z, r

    def test_flujo_completo(self):
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar()
        self.assertEqual(z.estado, 'COTIZADA')
        self.assertEqual((z.precio, z.dias_entrega, z.total), (Decimal('55.00'), 3, Decimal('63.25')))  # días del proveedor, IVA 15 %
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='$63.25').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='3 días').exists())
        # el cliente ve el total de la orden con la pieza
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, '$28.75 + $63.25 = $92.00')
        self.assertNotContains(r, 'Compu Mundo')  # el proveedor y el costo son del administrador
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        z.refresh_from_db()
        self.assertEqual(z.estado, 'ACEPTADA')
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='Pídela al proveedor').exists())
        # el administrador la pide y le llega al cliente la fecha
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_pedida', args=[self.o.pk, z.pk]))
        z.refresh_from_db()
        self.assertEqual((z.estado, z.llegada_estimada), ('PEDIDA', timezone.localdate() + timedelta(days=3)))
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='Pedimos la pieza').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Se pidió al proveedor').exists())
        self.o.refresh_from_db()
        self.assertTrue(self.o.espera_repuesto)
        # el administrador registra la pieza en el inventario: eso es la llegada (sin pasos extra)
        self.registrar_por_formulario(z)
        z.refresh_from_db()
        self.o.refresh_from_db()
        self.assertEqual((z.estado, self.o.estado), ('DISPONIBLE', 'REPARACION'))
        self.assertFalse(self.o.espera_repuesto)
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Llegó la pieza').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='Ya llegó la pieza').exists())
        self.assertEqual(presupuestos.total_orden(self.o), Decimal('92.00'))

    def test_precio_sugerido_con_el_margen_si_solo_hay_costo(self):
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar(precio='', costo='40')
        self.assertEqual(z.precio, Decimal('52.00'))  # 40 + 30 %

    def test_dias_manuales_mandan_sobre_los_del_proveedor(self):
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar(dias_entrega='7')
        self.assertEqual(z.dias_entrega, 7)

    def test_cotizacion_invalida(self):
        self.aceptar_proforma()
        self.pedir()
        for malo in ({'precio': '', 'costo': ''}, {'precio': '30', 'costo': '40'}, {'precio': 'abc'}, {'precio': '-5'}, {'dias_entrega': '500'}):
            z, _ = self.cotizar(**malo)
            self.assertEqual(z.estado, 'PENDIENTE', malo)

    def test_el_cliente_rechaza_y_se_libera_la_espera(self):
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar()
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'rechazar'})
        z.refresh_from_db()
        self.o.refresh_from_db()
        self.assertEqual(z.estado, 'RECHAZADA')
        self.assertFalse(self.o.espera_repuesto)
        self.assertEqual(presupuestos.total_orden(self.o), Decimal('28.75'))
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='rechazó la pieza').exists())

    def test_admin_decide_no_pedir(self):
        self.aceptar_proforma()
        self.pedir()
        z = PiezaSolicitada.objects.get()
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_descartar', args=[self.o.pk, z.pk]), {'nota_admin': 'Ese modelo ya no se vende'})
        z.refresh_from_db()
        self.o.refresh_from_db()
        self.assertEqual(z.estado, 'DESCARTADA')
        self.assertFalse(self.o.espera_repuesto)
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Ese modelo ya no se vende').exists())

    def test_orden_de_los_pasos_se_respeta(self):
        self.aceptar_proforma()
        self.pedir()
        z = PiezaSolicitada.objects.get()
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_pedida', args=[self.o.pk, z.pk]))       # sin aceptar
        z.refresh_from_db()
        self.assertEqual(z.estado, 'PENDIENTE')
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})  # sin cotizar
        z.refresh_from_db()
        self.assertEqual(z.estado, 'PENDIENTE')

    def test_permisos(self):
        self.aceptar_proforma()
        self.pedir()
        z = PiezaSolicitada.objects.get()
        otro = type(self.cli).objects.create_user('o@t.com', 'Otro Cli', 'Taller-2026-ok', rol='CLIENTE')
        for u in (self.tec, self.cli):
            self.client.force_login(u)
            for nombre in ('pieza_cotizar', 'pieza_descartar', 'pieza_pedida'):
                self.client.post(reverse(nombre, args=[self.o.pk, z.pk]), {'precio': '10'})
        z.refresh_from_db()
        self.assertEqual(z.estado, 'PENDIENTE')
        self.client.force_login(self.admin)
        z, _ = self.cotizar()
        for u in (otro, self.tec, self.admin):
            self.client.force_login(u)
            r = self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
            self.assertEqual(r.status_code, 404)
        z.refresh_from_db()
        self.assertEqual(z.estado, 'COTIZADA')

    def test_dos_piezas_la_espera_sigue_hasta_resolver_las_dos(self):
        self.aceptar_proforma()
        self.pedir()
        self.pedir(nombre='Cargador 65W')
        primera = PiezaSolicitada.objects.order_by('pk').first()
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_descartar', args=[self.o.pk, primera.pk]), {})
        self.o.refresh_from_db()
        self.assertTrue(self.o.espera_repuesto)
        segunda = PiezaSolicitada.objects.order_by('pk').last()
        self.client.post(reverse('pieza_descartar', args=[self.o.pk, segunda.pk]), {})
        self.o.refresh_from_db()
        self.assertFalse(self.o.espera_repuesto)

    def test_el_admin_ve_el_formulario_de_cotizar(self):
        self.aceptar_proforma()
        self.pedir()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Cotizar esta pieza')
        self.assertContains(r, 'Compu Mundo · 3 días')

    def test_proforma_aceptada_no_dice_que_salieron_repuestos(self):
        self.aceptar_proforma()
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertNotContains(r, 'repuestos ya salieron')
        self.assertContains(r, 'Factura general')

    # ---- fecha de entrega
    def a_pedida(self, dias='1', fecha=None):
        if fecha is not None:
            self.o.fecha_estimada = fecha
            self.o.save()
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar(dias_entrega=dias)
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        self.client.force_login(self.admin)
        self.client.post(reverse('pieza_pedida', args=[self.o.pk, z.pk]))
        self.o.refresh_from_db()
        return z

    def test_la_fecha_estimada_se_atrasa_por_la_pieza(self):
        hoy = timezone.localdate()
        self.a_pedida(dias='1', fecha=hoy + timedelta(days=1))
        self.assertEqual(self.o.fecha_estimada, hoy + timedelta(days=2))  # llega mañana + un día para instalar
        n = Notificacion.objects.get(usuario=self.cli, texto__contains='Actualización de fecha')
        self.assertIn(f'{hoy + timedelta(days=2):%d/%m/%Y}', n.texto)
        self.assertIn(f'antes {hoy + timedelta(days=1):%d/%m/%Y}', n.texto)
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='ahora es el').exists())
        self.assertTrue(self.o.historial.filter(evento__contains='(por la pieza)').exists())

    def test_la_fecha_no_se_adelanta_si_ya_sobra_tiempo(self):
        hoy = timezone.localdate()
        self.a_pedida(dias='2', fecha=hoy + timedelta(days=10))
        self.assertEqual(self.o.fecha_estimada, hoy + timedelta(days=10))
        self.assertFalse(Notificacion.objects.filter(texto__contains='Actualización de fecha').exists())

    def test_sin_fecha_previa_se_calcula(self):
        hoy = timezone.localdate()
        self.a_pedida(dias='3')
        self.assertEqual(self.o.fecha_estimada, hoy + timedelta(days=4))

    def test_el_cliente_ve_la_nueva_fecha_antes_de_aceptar(self):
        hoy = timezone.localdate()
        self.o.fecha_estimada = hoy + timedelta(days=1)
        self.o.save()
        self.aceptar_proforma()
        self.pedir()
        self.cotizar(dias_entrega='1')
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, f'pasará del {(hoy + timedelta(days=1)):%d/%m/%Y} al')
        self.assertContains(r, f'{(hoy + timedelta(days=2)):%d/%m/%Y}')

    # ---- registrar la pieza en el inventario
    def test_nuevo_producto_viene_lleno_desde_la_pieza(self):
        from inventario.models import Categoria
        cat = Categoria.objects.create(nombre='Computadoras')
        self.prov.categorias.add(cat)
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar()
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Pedir al proveedor')
        self.assertNotContains(r, f'?pieza={z.pk}')
        self.client.post(reverse('pieza_pedida', args=[self.o.pk, z.pk]))
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, f'?pieza={z.pk}')
        r = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk})
        ini = r.context['form'].initial
        self.assertEqual((ini['nombre'], ini['proveedor1'], ini['precio1'], ini['precio_venta'], ini['categoria'], ini['stock_inicial']),
                         ('Pantalla 15.6 LED', self.prov.pk, Decimal('40.00'), Decimal('55.00'), cat.pk, 0))
        self.assertContains(r, 'Datos tomados de la pieza')

    def test_guardar_enlaza_la_pieza_con_el_producto(self):
        from inventario.models import Producto
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar()
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        self.client.force_login(self.admin)
        r = self.client.post(reverse('producto_nuevo'), {
            'pieza': z.pk, 'nombre': 'Pantalla 15.6 LED', 'codigo': 'PAN-156', 'marca': '', 'categoria': '', 'precio_venta': '55',
            'costo_pedido': '', 'stock_minimo': '1', 'stock_inicial': '0', 'margen_propio': '',
            'proveedor1': self.prov.pk, 'precio1': '40', 'proveedor2': '', 'precio2': '', 'activo': 'on',
        })
        self.assertEqual(r.status_code, 302)
        z.refresh_from_db()
        self.assertEqual(z.producto, Producto.objects.get(codigo='PAN-156'))
        # ya enlazada: no se ofrece de nuevo ni llena otro formulario
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertNotContains(r, f'?pieza={z.pk}')
        self.assertContains(r, 'En el inventario')
        ini = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk}).context['form'].initial
        self.assertNotIn('proveedor1', ini)

    def test_pieza_sin_aceptar_no_llena_el_formulario(self):
        self.aceptar_proforma()
        self.pedir()
        z = PiezaSolicitada.objects.get()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk})
        self.assertNotContains(r, 'Datos tomados de la pieza')
        r = self.client.get(reverse('producto_nuevo'), {'pieza': 'abc'})
        self.assertEqual(r.status_code, 200)

    # ---- categoría de la pieza
    def test_el_tecnico_elige_la_categoria_y_llega_al_formulario_del_producto(self):
        from inventario.models import Categoria
        cat = Categoria.objects.create(nombre='Pantallas')
        otra = Categoria.objects.create(nombre='Computadoras')
        self.prov.categorias.add(otra)  # el proveedor sí es de otra categoría: manda la del técnico
        self.aceptar_proforma()
        self.client.force_login(self.tec)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Categoría (si la sabes)')
        self.client.post(reverse('pieza_pedir', args=[self.o.pk]), {'nombre_pieza': 'Pantalla 15.6 LED', 'categoria': cat.pk, 'cantidad_pieza': '1'})
        z = PiezaSolicitada.objects.get()
        self.assertEqual(z.categoria, cat)
        z, _ = self.cotizar()
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        self.client.force_login(self.admin)
        ini = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk}).context['form'].initial
        self.assertEqual(ini['categoria'], cat.pk)

    def test_la_cotizacion_muestra_primero_los_proveedores_de_la_categoria(self):
        from inventario.models import Categoria
        cat = Categoria.objects.create(nombre='Pantallas')
        self.prov.categorias.add(cat)
        ajeno = Proveedor.objects.create(nombre='Otro Proveedor')
        self.aceptar_proforma()
        self.client.force_login(self.tec)
        self.client.post(reverse('pieza_pedir', args=[self.o.pk]), {'nombre_pieza': 'Pantalla 15.6 LED', 'categoria': cat.pk, 'cantidad_pieza': '1'})
        self.client.force_login(self.admin)
        html = self.client.get(reverse('orden_admin', args=[self.o.pk])).content.decode()
        self.assertIn('Proveedores de Pantallas', html)
        self.assertLess(html.index('Compu Mundo · 3 días'), html.index('Otros proveedores'))
        self.assertLess(html.index('Otros proveedores'), html.index('Otro Proveedor'))
        self.assertEqual(ajeno.categorias.count(), 0)

    def test_categoria_invalida_o_vacia_se_ignora(self):
        self.aceptar_proforma()
        self.client.force_login(self.tec)
        self.client.post(reverse('pieza_pedir', args=[self.o.pk]), {'nombre_pieza': 'Pantalla 15.6 LED', 'categoria': '9999', 'cantidad_pieza': '1'})
        self.client.post(reverse('pieza_pedir', args=[self.o.pk]), {'nombre_pieza': 'Cargador 65W', 'categoria': '', 'cantidad_pieza': '1'})
        self.assertEqual(PiezaSolicitada.objects.filter(categoria__isnull=True).count(), 2)

    # ---- llegada: entra al inventario y la orden pasa a reparación; el técnico la registra como usada
    def llegada(self, dias='1', stock=0):
        z = self.a_pedida(dias=dias)
        prod = self.registrar_por_formulario(z)
        prod.refresh_from_db()
        self.o.refresh_from_db()
        z.refresh_from_db()
        return z, prod

    def test_al_llegar_entra_al_inventario_y_la_orden_pasa_a_reparacion(self):
        z, prod = self.llegada()
        self.assertEqual(prod.stock, 1)  # entró; todavía no se usa
        movs = list(prod.movimientos.order_by('pk'))
        self.assertEqual([(m.tipo, m.cantidad, m.stock_resultante) for m in movs], [('ENTRADA', 1, 1)])
        self.assertIn(f'orden #{self.o.pk}', movs[0].pedido.observacion)
        self.assertEqual((z.estado, self.o.estado), ('DISPONIBLE', 'REPARACION'))
        self.assertTrue(self.o.historial.filter(evento__contains='automático').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='En reparación').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='regístrala como usada').exists())

    def test_el_estado_sigue_en_diagnostico_hasta_que_llegue_y_se_registre(self):
        self.aceptar_proforma()
        self.pedir()
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')
        self.client.force_login(self.tec)
        self.client.post(reverse('tecnico_estado', args=[self.o.pk]), {'estado': 'REPARACION'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'DIAGNOSTICO')  # hay una pieza en trámite
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Hay una pieza en trámite')

    def test_el_tecnico_usa_la_pieza_y_sale_del_inventario(self):
        z, prod = self.llegada()
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Usar esta pieza')
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        prod.refresh_from_db()
        self.assertEqual(prod.stock, 0)
        self.assertEqual([m.tipo for m in prod.movimientos.order_by('pk')], ['ENTRADA', 'SALIDA'])
        self.assertEqual(prod.movimientos.order_by('pk').last().motivo, f'Usado en la orden #{self.o.pk}')
        uso = self.o.usos_repuesto.get()
        self.assertEqual((uso.cantidad, uso.pieza), (1, z))
        self.assertFalse(Notificacion.objects.filter(usuario=self.admin, texto__contains='Agotado').exists())
        self.assertNotContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Usar esta pieza')

    def test_no_se_usa_dos_veces_la_misma_pieza(self):
        z, prod = self.llegada()
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        prod.refresh_from_db()
        self.assertEqual(self.o.usos_repuesto.count(), 1)
        self.assertEqual(prod.stock, 0)

    def test_no_marca_listo_sin_registrar_la_pieza_usada(self):
        z, prod = self.llegada()
        self.client.force_login(self.tec)
        self.client.post(reverse('tecnico_estado', args=[self.o.pk]), {'estado': 'LISTO'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'REPARACION')
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        self.client.post(reverse('tecnico_estado', args=[self.o.pk]), {'estado': 'LISTO'})
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')

    def test_el_tecnico_busca_otro_repuesto_en_el_inventario(self):
        from inventario.models import Producto
        z, prod = self.llegada()
        otro = Producto.objects.create(nombre='Pasta térmica', codigo='PT-1', precio_venta=Decimal('5'), costo_pedido=Decimal('2'), stock=4, stock_minimo=1)
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Buscar otro repuesto en el inventario')
        self.assertContains(r, 'Pasta térmica')
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'producto': otro.pk, 'cantidad': '2'})
        otro.refresh_from_db()
        self.assertEqual(otro.stock, 2)
        # más de lo que hay: se rechaza y no cambia nada
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'producto': otro.pk, 'cantidad': '5'})
        otro.refresh_from_db()
        self.assertEqual(otro.stock, 2)

    def test_quitar_un_uso_devuelve_al_inventario(self):
        z, prod = self.llegada()
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        uso = self.o.usos_repuesto.get()
        self.client.post(reverse('repuesto_quitar', args=[self.o.pk, uso.pk]))
        prod.refresh_from_db()
        self.assertEqual(prod.stock, 1)
        self.assertFalse(self.o.usos_repuesto.exists())
        self.assertEqual(prod.movimientos.order_by('pk').last().tipo, 'ENTRADA')

    def test_solo_el_tecnico_de_la_orden_y_en_reparacion(self):
        z, prod = self.llegada()
        for u in (self.tec2, self.cli, self.admin):
            self.client.force_login(u)
            r = self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
            self.assertEqual(r.status_code, 404)
        Orden = type(self.o)
        Orden.objects.filter(pk=self.o.pk).update(estado='DIAGNOSTICO', espera_repuesto=False)
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        self.assertFalse(self.o.usos_repuesto.exists())

    def test_en_reparacion_el_tecnico_puede_seguir_presupuestando(self):
        self.o.estado = 'REPARACION'
        self.o.save()
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Armar presupuesto')
        self.assertContains(r, 'Listo')  # puede marcar que ya terminó

    def test_al_marcar_listo_el_cliente_recibe_el_total_a_pagar(self):
        z, prod = self.llegada()
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})
        self.client.post(reverse('tecnico_estado', args=[self.o.pk]), {'estado': 'LISTO'})
        n = Notificacion.objects.get(usuario=self.cli, texto__contains='está listo')
        self.assertIn('Total a pagar: $92.00', n.texto)

    def test_cotizar_exige_proveedor_y_costo(self):
        self.aceptar_proforma()
        self.pedir()
        for malo in ({'proveedor': ''}, {'costo': ''}, {'costo': '0'}):
            z, _ = self.cotizar(**malo)
            self.assertEqual(z.estado, 'PENDIENTE', malo)

    def test_el_formulario_del_producto_trae_proveedor_y_precio_seleccionados(self):
        from inventario.models import Categoria
        cat = Categoria.objects.create(nombre='Pantallas')  # categoría que el proveedor NO tiene: igual debe verse
        self.aceptar_proforma()
        self.client.force_login(self.tec)
        self.client.post(reverse('pieza_pedir', args=[self.o.pk]), {'nombre_pieza': 'Pantalla 15.6 LED', 'categoria': cat.pk, 'cantidad_pieza': '1'})
        z, _ = self.cotizar()
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        self.client.force_login(self.admin)
        html = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk}).content.decode()
        self.assertRegex(html, rf'<option value="{self.prov.pk}"[^>]*selected')
        self.assertRegex(html, r'name="precio1"[^>]*value="40(\.00)?"')

    # ---- registrar = llegada; factura general
    def test_registrar_la_pieza_es_la_llegada_y_no_duplica_stock(self):
        z = self.a_pedida(dias='1')
        prod = self.registrar_por_formulario(z)
        self.assertEqual(prod.stock, 1)  # entró solo la pieza; no hay campo de stock inicial que lo duplique
        self.assertEqual([m.tipo for m in prod.movimientos.order_by('pk')], ['ENTRADA'])

    def test_el_formulario_de_la_pieza_no_pide_stock_inicial(self):
        z = self.a_pedida(dias='1')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk})
        self.assertNotIn('stock_inicial', r.context['form'].fields)
        self.assertContains(r, 'se da por llegada')

    def test_el_admin_ya_no_ve_el_boton_llego_la_pieza_aparte(self):
        z = self.a_pedida(dias='1')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Llegó la pieza: registrarla en el inventario')
        self.assertNotContains(r, 'pieza_disponible')
        self.assertNotContains(r, 'Marcar disponible')

    def test_registrar_directo_desde_aceptada_tambien_vale(self):
        self.aceptar_proforma()
        self.pedir()
        z, _ = self.cotizar()
        self.client.force_login(self.cli)
        self.client.post(reverse('pieza_responder', args=[self.o.pk, z.pk]), {'respuesta': 'aceptar'})
        self.registrar_por_formulario(z)  # sin marcar «Ya la pedí»
        z.refresh_from_db()
        self.o.refresh_from_db()
        self.assertEqual((z.estado, self.o.estado), ('DISPONIBLE', 'REPARACION'))
        self.assertIsNotNone(z.pedida_en)

    def test_pieza_pendiente_o_cotizada_no_se_registra(self):
        self.aceptar_proforma()
        self.pedir()
        z = PiezaSolicitada.objects.get()
        self.client.force_login(self.admin)
        r = self.client.get(reverse('producto_nuevo'), {'pieza': z.pk})
        self.assertNotContains(r, 'Datos tomados de la pieza')

    def test_la_factura_junta_proforma_pieza_y_adicionales(self):
        from inventario.models import Producto
        z = self.a_pedida(dias='1')
        self.registrar_por_formulario(z)
        extra = Producto.objects.create(nombre='Pasta térmica', codigo='PT-1', precio_venta=Decimal('10'), costo_pedido=Decimal('4'), stock=5, stock_minimo=1)
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'pieza': z.pk})          # ya cobrada: no suma otra vez
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'producto': extra.pk, 'cantidad': '2'})  # adicional: $20 + IVA
        f = self.o.factura
        self.assertEqual([(l['detalle'], l['tipo']) for l in f['lineas']],
                         [('Diagnóstico', 'Servicio'), ('Mano de obra', 'Servicio'), ('Pantalla 15.6 LED', 'Pieza'), ('Pasta térmica', 'Repuesto adicional')])
        self.assertEqual(f['subtotal'], Decimal('5') + Decimal('20') + Decimal('55') + Decimal('20'))  # 100
        self.assertEqual(f['iva'], Decimal('3.75') + Decimal('8.25') + Decimal('3.00'))               # 15.00
        self.assertEqual(f['total'], Decimal('115.00'))
        self.assertEqual(self.o.total_a_pagar, Decimal('115.00'))

    def test_el_tecnico_y_el_cliente_ven_factura_general_y_no_el_presupuesto_aceptado(self):
        z = self.a_pedida(dias='1')
        for u in (self.tec, self.cli, self.admin):
            self.client.force_login(u)
            url = reverse('orden_admin' if u == self.admin else 'equipo', args=[self.o.pk])
            r = self.client.get(url)
            self.assertContains(r, 'Factura general')
            self.assertContains(r, 'Total a pagar')
            self.assertNotContains(r, 'Aceptar presupuesto')
            self.assertNotContains(r, 'Esta proforma incluye solo el diagnóstico')  # la tarjeta del presupuesto aceptado ya no se muestra

    def test_un_presupuesto_pendiente_sigue_apareciendo_para_responderlo(self):
        self.proforma()
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Aceptar presupuesto')
        self.assertNotContains(r, 'Factura general')

    def test_presupuesto_rechazado_no_entra_en_la_factura(self):
        self.proforma()
        p = Presupuesto.objects.get()
        self.client.force_login(self.cli)
        self.client.post(reverse('presupuesto_responder', args=[self.o.pk, p.pk]), {'respuesta': 'rechazar'})
        self.assertEqual(self.o.factura['total'], Decimal('0'))
        self.assertEqual(self.o.factura['lineas'], [])
