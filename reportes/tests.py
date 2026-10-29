from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finanzas.models import Gasto
from inventario.models import Producto
from ordenes import presupuestos
from ordenes.models import Orden, Pago, Servicio, Tarifa
from usuarios.models import Usuario

from . import datos, salida

CLAVE = 'Taller-2026-ok'


class ReportesTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin', CLAVE)
        self.cli = Usuario.objects.create_user('c@t.com', 'Ana Cliente', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@t.com', 'Tec', CLAVE, rol='TECNICO')
        self.s = Servicio.objects.create(nombre='Computadoras', precio_base=10)
        self.mano = Tarifa.objects.create(servicio=None, nombre='Mano de obra', precio=Decimal('20'))
        self.o = Orden.objects.create(cliente=self.cli, servicio=self.s, modelo='HP 15', problema='x', tecnico=self.tec, estado='DIAGNOSTICO')
        p = presupuestos.crear(self.o.pk, self.tec, {self.mano.pk: 1}, [], 'ok')
        presupuestos.responder(p.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='LISTO')
        Pago.objects.create(orden=self.o, metodo='TRANSFERENCIA', monto=Decimal('23'), estado='CONFIRMADO', comprobante='c.png', referencia='=CMD()',
                            validado_por=self.admin, validado_en=timezone.now())
        Gasto.objects.create(concepto='+Luz', monto=Decimal('10'), usuario=self.admin)
        Producto.objects.create(nombre='RAM', codigo='R1', precio_venta=30, costo_pedido=Decimal('20'), stock=2, stock_minimo=3)
        self.client.force_login(self.admin)

    def bajar(self, clave, formato, **q):
        return self.client.get(reverse('reporte_descargar', args=[clave, formato]), q)

    def test_todos_los_reportes_en_pdf_y_csv(self):
        for clave in datos.REPORTES:
            r = self.bajar(clave, 'pdf')
            self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'), clave)
            self.assertTrue(r.content.startswith(b'%PDF'), clave)
            r = self.bajar(clave, 'csv')
            self.assertEqual(r.status_code, 200, clave)
            self.assertIn('text/csv', r['Content-Type'])

    def test_contenido_csv(self):
        txt = self.bajar('ingresos', 'csv').content.decode('utf-8-sig')
        self.assertIn('Ana Cliente', txt)
        self.assertIn("'=CMD()", txt)             # no se ejecuta como fórmula en Excel
        self.assertIn('Total cobrado;$23.00', txt)
        self.assertIn("'+Luz", self.bajar('gastos', 'csv').content.decode('utf-8-sig'))
        inv = self.bajar('inventario', 'csv').content.decode('utf-8-sig')
        self.assertIn('Stock bajo', inv)
        self.assertIn('Valor del inventario activo;$40.00', inv)

    def test_estado_financiero(self):
        rep = datos.estado_financiero(*datos.leer_rango('', ''))
        v = {f[0]: f[1] for f in rep.filas}
        self.assertEqual(v['Ingresos cobrados en el período'], '23.00')
        self.assertEqual(v['(=) Utilidad del período'], '13.00')

    def test_rango_invalido_o_fuera_de_rango(self):
        r = self.bajar('ingresos', 'csv', desde='2026-10-10', hasta='2026-10-01')
        self.assertRedirects(r, reverse('reportes'), fetch_redirect_response=False)
        self.assertEqual(self.bajar('ingresos', 'csv', desde='basura').status_code, 302)
        self.assertEqual(self.bajar('ingresos', 'csv', desde='2000-01-01', hasta='2026-01-01').status_code, 302)
        mes_pasado = (timezone.localdate() - timedelta(days=90)).isoformat()
        txt = self.bajar('ingresos', 'csv', desde=mes_pasado, hasta=mes_pasado).content.decode('utf-8-sig')
        self.assertNotIn('Ana Cliente', txt)

    def test_claves_invalidas(self):
        self.assertEqual(self.bajar('nada', 'pdf').status_code, 404)
        self.assertEqual(self.bajar('gastos', 'exe').status_code, 404)

    def test_solo_admin(self):
        for u in (self.cli, self.tec):
            self.client.force_login(u)
            self.assertNotEqual(self.client.get(reverse('reportes')).status_code, 200)
            self.assertNotEqual(self.bajar('ingresos', 'csv').status_code, 200)
        self.client.logout()
        self.assertNotEqual(self.bajar('ingresos', 'csv').status_code, 200)

    def test_pagina_de_reportes(self):
        r = self.client.get(reverse('reportes'))
        self.assertContains(r, 'Estado financiero')
        self.assertContains(r, 'Descargar Excel')

    def test_factura_pdf(self):
        url = reverse('factura_pdf', args=[self.o.pk])
        self.assertTrue(self.client.get(url).content.startswith(b'%PDF'))
        self.client.force_login(self.cli)
        r = self.client.get(url)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'descargar factura')
        otro = Usuario.objects.create_user('o@t.com', 'Otro', CLAVE, rol='CLIENTE')
        self.client.force_login(otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.tec)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'descargar factura')

    def test_factura_sin_nada_facturado(self):
        vacia = Orden.objects.create(cliente=self.cli, servicio=self.s, modelo='X', problema='y')
        r = self.client.get(reverse('factura_pdf', args=[vacia.pk]))
        self.assertEqual(r.status_code, 302)

    def test_texto_con_simbolos_en_pdf(self):
        Gasto.objects.create(concepto='Tinta <b>&</b> papel ñandú', monto=Decimal('5'), usuario=self.admin)
        self.assertTrue(self.bajar('gastos', 'pdf').content.startswith(b'%PDF'))
