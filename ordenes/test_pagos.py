import io
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from comunicacion.models import Notificacion
from core.models import Configuracion

from . import presupuestos
from .models import Orden, Pago, Tarifa
from .test_tecnico import Base

MEDIA = tempfile.mkdtemp()


def foto(nombre='comp.png', formato='PNG'):
    b = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(b, formato)
    return SimpleUploadedFile(nombre, b.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA)
class PagosTests(Base):
    def setUp(self):
        super().setUp()
        self.mano = Tarifa.objects.create(servicio=None, nombre='Mano de obra', precio=Decimal('20.00'))
        self.o = self.orden(self.tec, estado='DIAGNOSTICO')
        p = presupuestos.crear(self.o.pk, self.tec, {self.mano.pk: 1}, [], 'Cambio de pasta')
        presupuestos.responder(p.pk, self.cli, True)
        Orden.objects.filter(pk=self.o.pk).update(estado='LISTO')
        self.o.refresh_from_db()
        self.total = self.o.total_a_pagar

    def enviar(self, archivo=None, referencia='123456'):
        self.client.force_login(self.cli)
        datos = {'referencia': referencia}
        if archivo is not False:
            datos['comprobante'] = archivo or foto()
        return self.client.post(reverse('pago_enviar', args=[self.o.pk]), datos)

    def test_total_mayor_a_cero(self):
        self.assertGreater(self.total, 0)

    def test_cliente_envia_comprobante_y_admin_es_avisado(self):
        self.enviar()
        p = self.o.pagos.get()
        self.assertEqual((p.estado, p.metodo, p.monto), ('PENDIENTE', 'TRANSFERENCIA', self.total))
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='comprobante').exists())

    def test_no_se_puede_enviar_dos_veces_ni_antes_de_estar_listo(self):
        self.enviar()
        self.enviar()
        self.assertEqual(self.o.pagos.count(), 1)
        Orden.objects.filter(pk=self.o.pk).update(estado='REPARACION')
        Pago.objects.all().delete()
        self.enviar()
        self.assertEqual(self.o.pagos.count(), 0)

    def test_archivos_invalidos(self):
        self.enviar(SimpleUploadedFile('x.png', b'no soy imagen'))
        self.enviar(SimpleUploadedFile('x.pdf', b'%PDF-1.4 ' + b'0' * (3 * 1024 * 1024)))
        self.enviar(False)
        self.enviar(referencia='  ')
        self.assertEqual(self.o.pagos.count(), 0)

    def test_pdf_valido(self):
        self.enviar(SimpleUploadedFile('c.pdf', b'%PDF-1.4 contenido'))
        self.assertEqual(self.o.pagos.count(), 1)

    def test_otro_cliente_no_paga_orden_ajena(self):
        from usuarios.models import Usuario
        otro = Usuario.objects.create_user('o@t.com', 'Otro', 'Taller-2026-ok', rol='CLIENTE')
        self.client.force_login(otro)
        r = self.client.post(reverse('pago_enviar', args=[self.o.pk]), {'referencia': '1234', 'comprobante': foto()})
        self.assertEqual(r.status_code, 404)

    def test_admin_confirma_y_autoriza_entrega(self):
        self.enviar()
        p = self.o.pagos.get()
        self.client.force_login(self.admin)
        self.client.post(reverse('pago_confirmar', args=[self.o.pk, p.pk]))
        p.refresh_from_db(); self.o.refresh_from_db()
        self.assertEqual(p.estado, 'CONFIRMADO')
        self.assertTrue(self.o.entrega_autorizada)
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='Recibimos tu pago').exists())
        self.client.post(reverse('entregar', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'ENTREGADO')
        self.assertIsNotNone(self.o.entregada_en)
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='fue entregado').exists())

    def test_confirmar_falla_si_el_total_cambio(self):
        self.enviar()
        p = self.o.pagos.get()
        Pago.objects.filter(pk=p.pk).update(monto=Decimal('1.00'))
        self.client.force_login(self.admin)
        self.client.post(reverse('pago_confirmar', args=[self.o.pk, p.pk]))
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE')

    def test_rechazo_pide_motivo_y_permite_reenviar(self):
        self.enviar()
        p = self.o.pagos.get()
        self.client.force_login(self.admin)
        self.client.post(reverse('pago_rechazar', args=[self.o.pk, p.pk]), {'motivo': 'x'})
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE')
        self.client.post(reverse('pago_rechazar', args=[self.o.pk, p.pk]), {'motivo': 'El monto no coincide'})
        p.refresh_from_db()
        self.assertEqual(p.estado, 'RECHAZADO')
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='El monto no coincide').exists())
        self.enviar()
        self.assertEqual(self.o.pagos.count(), 2)

    def test_cliente_elige_efectivo_y_admin_confirma(self):
        self.client.force_login(self.cli)
        self.client.post(reverse('pago_efectivo_cliente', args=[self.o.pk]))
        p = self.o.pagos.get()
        self.assertEqual((p.metodo, p.estado), ('EFECTIVO', 'PENDIENTE'))
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Acércate a las oficinas')
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='en efectivo').exists())
        self.client.post(reverse('pago_efectivo_cliente', args=[self.o.pk]))
        self.assertEqual(self.o.pagos.count(), 1)
        self.client.force_login(self.admin)
        self.client.post(reverse('pago_confirmar', args=[self.o.pk, p.pk]))
        self.o.refresh_from_db()
        self.assertTrue(self.o.entrega_autorizada)
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='pago de la orden').exists())

    def test_cliente_ve_las_dos_opciones(self):
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, '¿Cómo deseas pagar?')
        self.assertContains(r, 'Pagaré en efectivo')

    def test_efectivo(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('pago_efectivo', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertTrue(self.o.entrega_autorizada)
        p = self.o.pagos.get()
        self.assertEqual((p.metodo, p.estado, p.monto, p.validado_por), ('EFECTIVO', 'CONFIRMADO', self.total, self.admin))
        self.client.post(reverse('pago_efectivo', args=[self.o.pk]))
        self.assertEqual(self.o.pagos.count(), 1)

    def test_no_se_entrega_sin_pago(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('entregar', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertEqual(self.o.estado, 'LISTO')

    def test_tecnico_y_cliente_no_usan_acciones_del_admin(self):
        for u in (self.tec, self.cli):
            self.client.force_login(u)
            for nombre in ('pago_efectivo', 'entregar', 'entrega_sin_cobro'):
                self.client.post(reverse(nombre, args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertEqual((self.o.estado, self.o.pagos.count()), ('LISTO', 0))

    def test_sin_cobro_solo_si_total_cero(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('entrega_sin_cobro', args=[self.o.pk]))
        self.o.refresh_from_db()
        self.assertFalse(self.o.entrega_autorizada)
        o2 = self.orden(self.tec, estado='LISTO')
        self.client.post(reverse('entrega_sin_cobro', args=[o2.pk]))
        o2.refresh_from_db()
        self.assertTrue(o2.entrega_autorizada)
        self.client.post(reverse('entregar', args=[o2.pk]))
        o2.refresh_from_db()
        self.assertEqual(o2.estado, 'ENTREGADO')

    def test_comprobante_protegido(self):
        self.enviar()
        p = self.o.pagos.get()
        url = reverse('pago_comprobante', args=[self.o.pk, p.pk])
        self.client.force_login(self.cli)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.tec)
        self.assertEqual(self.client.get(url).status_code, 404)
        from usuarios.models import Usuario
        self.client.force_login(Usuario.objects.create_user('o@t.com', 'Otro', 'Taller-2026-ok', rol='CLIENTE'))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_media_no_sirve_comprobantes(self):
        self.enviar()
        p = self.o.pagos.get()
        self.assertEqual(self.client.get('/media/' + p.comprobante.name).status_code, 404)

    def test_pantallas(self):
        self.enviar()
        Configuracion.guardar_datos_transferencia('Banco X cta 123')
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Estamos revisándolo')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.o.pk]))
        self.assertContains(r, 'Confirmar pago')
        self.assertContains(self.client.get(reverse('ordenes')), 'comprobante de pago por validar')
        self.assertContains(self.client.get(reverse('tarifas')), 'Banco X cta 123')
        self.client.force_login(self.tec)
        self.assertNotContains(self.client.get(reverse('equipo', args=[self.o.pk])), 'Pago y entrega')

    def test_cliente_ve_como_pagar(self):
        Configuracion.guardar_datos_transferencia('Banco X cta 123')
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[self.o.pk]))
        self.assertContains(r, 'Banco X cta 123')
        self.assertContains(r, 'Enviar comprobante')

    def test_guardar_datos_cuenta(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('transferencia_guardar'), {'datos': 'Banco Y 999'})
        self.assertEqual(Configuracion.datos_transferencia(), 'Banco Y 999')
        self.client.post(reverse('transferencia_guardar'), {'datos': 'x' * 300})
        self.assertEqual(Configuracion.datos_transferencia(), 'Banco Y 999')
        self.client.force_login(self.cli)
        self.client.post(reverse('transferencia_guardar'), {'datos': 'hack'})
        self.assertEqual(Configuracion.datos_transferencia(), 'Banco Y 999')
