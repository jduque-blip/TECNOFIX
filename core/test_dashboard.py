from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finanzas.models import Gasto
from inventario.models import MovimientoInventario, Producto
from ordenes.models import Orden, Pago, Servicio
from usuarios.models import Usuario

from . import dashboard

CLAVE = 'Taller-2026-ok'


class DashboardTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin', CLAVE)
        self.cli = Usuario.objects.create_user('c@t.com', 'Cli', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@t.com', 'Tec', CLAVE, rol='TECNICO')
        self.s = Servicio.objects.create(nombre='Computadoras', precio_base=10)

    def orden(self, estado='RECIBIDO', tecnico=None):
        return Orden.objects.create(cliente=self.cli, servicio=self.s, modelo='HP', problema='x', estado=estado, tecnico=tecnico)

    def test_sin_datos_no_falla_y_trae_los_once_graficos(self):
        kpis, graf = dashboard.datos()
        self.assertEqual(len(graf), 11)
        self.assertEqual(len(dashboard.TARJETAS), 11)
        self.assertEqual({t[0] for t in dashboard.TARJETAS}, set(graf))
        self.assertEqual((kpis['activas'], kpis['caja']), (0, 0.0))
        self.assertEqual(len(graf['ingresos_gastos']['etiquetas']), 6)

    def test_numeros(self):
        from core.models import Configuracion
        Configuracion.guardar_capital(Decimal('1000'))
        o = self.orden('LISTO', self.tec)
        self.orden('REPARACION', self.tec)
        self.orden()
        self.orden('LISTO', self.tec)  # lista y sin pagar
        Pago.objects.create(orden=o, metodo='EFECTIVO', monto=Decimal('150'), estado='CONFIRMADO', validado_por=self.admin, validado_en=timezone.now())
        Gasto.objects.create(concepto='Luz', monto=Decimal('40'), usuario=self.admin)
        Gasto.objects.create(tipo='PERDIDA', concepto='Dañado', monto=Decimal('15'), automatico=True)
        prod = Producto.objects.create(nombre='RAM', codigo='R1', precio_venta=30, stock=1, stock_minimo=2)
        MovimientoInventario.objects.create(producto=prod, tipo='SALIDA', cantidad=3, stock_resultante=1, motivo='uso')
        kpis, g = dashboard.datos()
        self.assertEqual((kpis['activas'], kpis['sin_tecnico'], kpis['stock_bajo']), (4, 1, 1))
        self.assertEqual((kpis['ingresos_mes'], kpis['utilidad_mes']), (150.0, 110.0))
        self.assertEqual(kpis['caja'], 1000 + 150 - 40)  # la pérdida automática no resta de la caja
        self.assertEqual(kpis['por_cobrar'], 1)
        self.assertEqual(g['ingresos_gastos']['ingresos'][-1], 150.0)
        self.assertEqual(g['estados']['valores'], [1, 0, 1, 2, 0, 0, 0])
        self.assertEqual(g['mas_usados'], {'etiquetas': ['RAM'], 'valores': [3]})
        self.assertEqual(g['stock_bajo']['etiquetas'], ['RAM'])
        self.assertEqual(g['metodos']['valores'], [1, 0])
        self.assertEqual(dict(zip(g['composicion']['etiquetas'], g['composicion']['valores']))['Repuestos perdidos o dañados'], 15.0)
        self.assertEqual(sum(g['recibidas']['valores']), 4)
        self.assertEqual(dict(zip(g['carga']['etiquetas'], g['carga']['valores'])), {'Tec': 1})

    def test_pantalla_solo_para_el_admin(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('panel'))
        self.assertContains(r, 'data-grafico="ingresos_gastos"')
        self.assertContains(r, 'chart.umd.js')
        self.assertEqual(r.content.decode().count('data-grafico='), 11)
        self.client.force_login(self.cli)
        self.assertNotContains(self.client.get(reverse('panel')), 'data-grafico=')
        self.client.force_login(self.tec)
        self.assertNotContains(self.client.get(reverse('panel')), 'data-grafico=')

    def test_la_libreria_de_graficos_va_incluida(self):
        from django.contrib.staticfiles import finders
        self.assertTrue(finders.find('js/vendor/chart.umd.js'))
        self.assertTrue(finders.find('js/dashboard.js'))
