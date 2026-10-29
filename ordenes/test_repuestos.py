from decimal import Decimal

from django.urls import reverse

from comunicacion.models import Notificacion
from inventario.models import Categoria, Producto

from . import presupuestos
from .models import Orden, Tarifa
from .test_tecnico import Base


class RepuestoDelPresupuestoTests(Base):
    def setUp(self):
        super().setUp()
        self.cat = Categoria.objects.create(nombre='Pantallas')
        self.otra = Categoria.objects.create(nombre='Baterías')
        self.pantalla = Producto.objects.create(nombre='Pantalla 15.6', codigo='PAN1', stock=3, precio_venta=Decimal('50'), categoria=self.cat)
        self.bateria = Producto.objects.create(nombre='Batería HP', codigo='BAT1', stock=2, precio_venta=Decimal('30'), categoria=self.otra)
        self.mano = Tarifa.objects.create(servicio=None, nombre='Mano de obra', precio=Decimal('20'))
        self.o = self.orden(self.tec, estado='DIAGNOSTICO')

    def presupuesto_aceptado(self):
        p = presupuestos.crear(self.o.pk, self.tec, {self.mano.pk: 1}, [(self.pantalla.pk, 1)], '')
        presupuestos.responder(p.pk, self.cli, True)
        return p

    def test_el_diagnostico_ofrece_filtro_por_categoria(self):
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Todas las categorías')
        self.assertContains(r, 'Pantallas')
        self.assertContains(r, f'data-categoria="{self.cat.pk}"')

    def test_aceptar_descuenta_y_avisa_al_admin(self):
        self.presupuesto_aceptado()
        self.pantalla.refresh_from_db()
        self.assertEqual(self.pantalla.stock, 2)
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='Salió del inventario: 1 × Pantalla 15.6').exists())

    def test_marcar_en_uso(self):
        self.presupuesto_aceptado()
        item = self.o.presupuestos.get().items.get(producto=self.pantalla)
        self.client.force_login(self.tec)
        # todavía en diagnóstico: no se puede
        self.client.post(reverse('repuesto_marcar', args=[self.o.pk, item.pk]))
        item.refresh_from_db()
        self.assertIsNone(item.en_uso_en)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Marcar como en uso')
        self.client.post(reverse('repuesto_marcar', args=[self.o.pk, item.pk]))
        item.refresh_from_db()
        self.assertIsNotNone(item.en_uso_en)
        self.pantalla.refresh_from_db()
        self.assertEqual(self.pantalla.stock, 2)  # no vuelve a descontar
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='está usando 1 × Pantalla 15.6').exists())
        self.client.post(reverse('repuesto_marcar', args=[self.o.pk, item.pk]))
        self.assertEqual(Notificacion.objects.filter(texto__contains='está usando').count(), 1)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'En uso desde')

    def test_otro_tecnico_no_marca(self):
        self.presupuesto_aceptado()
        item = self.o.presupuestos.get().items.get(producto=self.pantalla)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        self.client.force_login(self.tec2)
        self.assertEqual(self.client.post(reverse('repuesto_marcar', args=[self.o.pk, item.pk])).status_code, 404)

    def test_usar_otro_repuesto_avisa_al_admin(self):
        self.presupuesto_aceptado()
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'producto': self.bateria.pk, 'cantidad': '1'})
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='Salió del inventario: 1 × Batería HP').exists())


    def test_repuesto_danado_es_perdida_y_sale_del_stock(self):
        from finanzas.models import Gasto
        self.bateria.costo_pedido = Decimal('18')
        self.bateria.save()
        self.presupuesto_aceptado()
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_usar', args=[self.o.pk]), {'producto': self.bateria.pk, 'cantidad': '1'})  # la batería ya la usa
        self.bateria.refresh_from_db()
        self.assertEqual(self.bateria.stock, 1)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Reportar un repuesto dañado')
        self.client.post(reverse('repuesto_danado', args=[self.o.pk]), {'producto': self.bateria.pk, 'cantidad': '1', 'motivo': ''})
        self.assertEqual(Gasto.objects.count(), 0)  # sin motivo no se reporta
        self.client.post(reverse('repuesto_danado', args=[self.o.pk]), {'producto': self.bateria.pk, 'cantidad': '1', 'motivo': 'Se rompió al instalarla'})
        self.bateria.refresh_from_db()
        self.assertEqual(self.bateria.stock, 1)  # la unidad dañada ya había salido: no se descuenta otra vez
        g = Gasto.objects.get()
        self.assertEqual((g.tipo, g.monto, g.automatico), ('PERDIDA', Decimal('18.00'), True))
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='Repuesto dañado').exists())
        self.assertTrue(self.o.historial.filter(evento__contains='Repuesto dañado').exists())

    def test_danado_no_aplica_a_otro_tecnico_ni_ordenes_listas(self):
        from finanzas.models import Gasto
        self.client.force_login(self.tec2)
        self.assertEqual(self.client.post(reverse('repuesto_danado', args=[self.o.pk]), {'producto': self.bateria.pk, 'motivo': 'Se rompió'}).status_code, 404)
        Orden.objects.filter(pk=self.o.pk).update(estado='LISTO')
        self.client.force_login(self.tec)
        self.client.post(reverse('repuesto_danado', args=[self.o.pk]), {'producto': self.bateria.pk, 'cantidad': '1', 'motivo': 'Se rompió al instalarla'})
        self.assertEqual(Gasto.objects.count(), 0)
