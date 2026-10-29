from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario import servicios as stock
from inventario.models import Producto, Proveedor
from ordenes.models import Orden, Pago, Servicio
from usuarios.models import Usuario

from .models import Gasto

CLAVE = 'Taller-2026-ok'


class GastosTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin', CLAVE)
        self.cli = Usuario.objects.create_user('c@t.com', 'Cli', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@t.com', 'Tec', CLAVE, rol='TECNICO')
        self.client.force_login(self.admin)

    def nuevo(self, **extra):
        d = {'tipo': 'GASTO', 'concepto': 'Pago de luz', 'monto': '25.50', 'fecha': timezone.localdate().isoformat()}
        d.update(extra)
        return self.client.post(reverse('gasto_nuevo'), d)

    def test_registrar_gasto(self):
        self.nuevo()
        g = Gasto.objects.get()
        self.assertEqual((g.concepto, g.monto, g.tipo, g.usuario, g.automatico), ('Pago de luz', Decimal('25.50'), 'GASTO', self.admin, False))
        self.assertContains(self.client.get(reverse('gastos')), 'Pago de luz')

    def test_validaciones(self):
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        for malo in ({'monto': '0'}, {'monto': '-5'}, {'monto': 'abc'}, {'monto': '9999999'}, {'concepto': ' a '},
                     {'fecha': manana}, {'fecha': '2001-01-01'}, {'tipo': 'XX'}, {'concepto': ''}):
            self.nuevo(**malo)
        self.assertEqual(Gasto.objects.count(), 0)

    def test_solo_admin(self):
        for u in (self.cli, self.tec):
            self.client.force_login(u)
            self.assertNotEqual(self.client.get(reverse('gastos')).status_code, 200)
            self.nuevo()
        self.assertEqual(Gasto.objects.count(), 0)

    def test_editar_y_eliminar(self):
        self.nuevo()
        g = Gasto.objects.get()
        self.client.post(reverse('gasto_editar', args=[g.pk]), {'tipo': 'PERDIDA', 'concepto': 'Equipo roto', 'monto': '10', 'fecha': g.fecha.isoformat()})
        g.refresh_from_db()
        self.assertEqual((g.tipo, g.concepto, g.monto), ('PERDIDA', 'Equipo roto', Decimal('10')))
        self.client.post(reverse('gasto_eliminar', args=[g.pk]))
        self.assertEqual(Gasto.objects.count(), 0)

    def test_reposicion_crea_gasto_automatico_que_no_se_borra(self):
        prov = Proveedor.objects.create(nombre='P1')
        prod = Producto.objects.create(nombre='RAM', codigo='R1', precio_venta=30, stock=0)
        stock.reponer(prod.pk, prov, 4, Decimal('12.50'), '', self.admin)
        g = Gasto.objects.get()
        self.assertEqual((g.monto, g.automatico, g.tipo), (Decimal('50.00'), True, 'GASTO'))
        self.assertIsNotNone(g.pedido)
        self.client.post(reverse('gasto_eliminar', args=[g.pk]))
        self.assertEqual(Gasto.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('gasto_editar', args=[g.pk])).status_code, 404)

    def test_faltante_de_inventario_es_perdida(self):
        prod = Producto.objects.create(nombre='RAM', codigo='R1', precio_venta=30, costo_pedido=Decimal('10'), stock=5)
        stock.ajustar(prod.pk, 3, 'Conteo', self.admin)
        g = Gasto.objects.get()
        self.assertEqual((g.tipo, g.monto, g.automatico), ('PERDIDA', Decimal('20'), True))
        stock.ajustar(prod.pk, 6, 'Conteo', self.admin)  # sobrante: no es pérdida
        self.assertEqual(Gasto.objects.count(), 1)

    def test_resumen_con_ingresos_y_filtros(self):
        self.nuevo(monto='30')
        self.nuevo(tipo='PERDIDA', concepto='Pantalla dañada', monto='10')
        s = Servicio.objects.create(nombre='S', precio_base=1)
        o = Orden.objects.create(cliente=self.cli, servicio=s, modelo='X', problema='Y', tecnico=self.tec)
        Orden.objects.filter(pk=o.pk).update(estado='LISTO')
        Pago.objects.create(orden=o, metodo='EFECTIVO', monto=Decimal('100'), estado='CONFIRMADO', validado_por=self.admin, validado_en=timezone.now())
        r = self.client.get(reverse('gastos'))
        self.assertEqual((r.context['total_gastos'], r.context['perdidas_manuales'], r.context['ingresos'], r.context['utilidad']),
                         (Decimal('30'), Decimal('10'), Decimal('100'), Decimal('60')))
        r = self.client.get(reverse('gastos'), {'tipo': 'PERDIDA'})
        self.assertContains(r, 'Pantalla dañada')
        self.assertNotContains(r, 'Pago de luz')
        r = self.client.get(reverse('gastos'), {'mes': '2020-01'})
        self.assertEqual(r.context['total_gastos'], 0)
        self.assertEqual(self.client.get(reverse('gastos'), {'mes': 'basura', 'tipo': 'zzz'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('gastos'), {'mes': 'todos'}).context['total_gastos'], Decimal('30'))

    def test_caja_capital_y_perdidas_automaticas_no_restan_dos_veces(self):
        from core.models import Configuracion
        Configuracion.guardar_capital(Decimal('1000'))
        prov = Proveedor.objects.create(nombre='P1')
        prod = Producto.objects.create(nombre='RAM', codigo='R1', precio_venta=30, stock=0)
        stock.reponer(prod.pk, prov, 4, Decimal('10'), '', self.admin)   # gasto automático 40
        stock.ajustar(prod.pk, 3, 'Conteo', self.admin)                    # pérdida automática 10 (informativa)
        self.nuevo(monto='30')                                             # gasto manual 30
        self.nuevo(tipo='PERDIDA', concepto='Robo de herramienta', monto='20')
        s = Servicio.objects.create(nombre='S', precio_base=1)
        o = Orden.objects.create(cliente=self.cli, servicio=s, modelo='X', problema='Y', tecnico=self.tec)
        Pago.objects.create(orden=o, metodo='EFECTIVO', monto=Decimal('200'), estado='CONFIRMADO', validado_por=self.admin, validado_en=timezone.now())
        c = self.client.get(reverse('gastos')).context
        self.assertEqual(c['caja'], Decimal('1000') + 200 - 40 - 30 - 20)
        self.assertEqual((c['perdidas_inventario'], c['perdidas_manuales']), (Decimal('10'), Decimal('20')))
        self.assertEqual(c['utilidad'], 200 - 40 - 30 - 20)
        self.assertEqual(c['valor_inventario'], Decimal('30'))  # 3 × 10

    def test_guardar_capital(self):
        from core.models import Configuracion
        self.client.post(reverse('capital_guardar'), {'capital': '2500,50'})
        self.assertEqual(Configuracion.capital_inicial(), Decimal('2500.50'))
        for malo in ('-1', 'abc', 'nan', '1e99', ''):
            self.client.post(reverse('capital_guardar'), {'capital': malo})
        self.assertEqual(Configuracion.capital_inicial(), Decimal('2500.50'))
        self.client.force_login(self.cli)
        self.client.post(reverse('capital_guardar'), {'capital': '1'})
        self.assertEqual(Configuracion.capital_inicial(), Decimal('2500.50'))
