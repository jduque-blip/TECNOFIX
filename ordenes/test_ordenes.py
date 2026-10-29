import shutil
import tempfile
from datetime import timedelta

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario

from .models import Orden, Servicio
from .tests import imagen

CLAVE = 'Taller-2026-ok'


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='tf_media_'))
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = Usuario.objects.create_superuser('admin@correo.com', 'Admin Uno', CLAVE)
        self.ana = Usuario.objects.create_user('ana@correo.com', 'Ana Pérez', CLAVE, rol='CLIENTE', telefono='0991234567')
        self.luis = Usuario.objects.create_user('luis@correo.com', 'Luis Mora', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('tec@correo.com', 'Tec Uno', CLAVE, rol='TECNICO')
        self.tec2 = Usuario.objects.create_user('tec2@correo.com', 'Tec Dos', CLAVE, rol='TECNICO')
        self.srv = Servicio.objects.create(nombre='Laptops', precio_base=40)

    def mensajes(self, r):
        return [str(m) for m in get_messages(r.wsgi_request)]

    def orden(self, cliente=None, **extra):
        return Orden.objects.create(cliente=cliente or self.ana, servicio=self.srv, modelo='HP 15', problema='No enciende la pantalla', **extra)

    def solicitud(self, **c):
        datos = {'servicio': self.srv.pk, 'modelo': 'HP Pavilion 15', 'problema': 'Se apaga solo al rato de usarla', **c}
        return self.client.post(reverse('solicitar'), datos)


class SolicitudTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def test_solicitud_correcta(self):
        r = self.solicitud()
        self.assertRedirects(r, reverse('panel'))
        o = Orden.objects.get()
        self.assertEqual((o.cliente, o.estado, o.tecnico, o.modelo), (self.ana, 'RECIBIDO', None, 'HP Pavilion 15'))
        self.assertEqual(o.historial.get().evento, 'Solicitud creada por el cliente')

    def test_con_fotos(self):
        self.solicitud(fotos=[imagen('a.png'), imagen('b.png')])
        o = Orden.objects.get()
        self.assertEqual(o.fotos.count(), 2)
        for f in o.fotos.all():
            self.assertTrue(f.imagen.name.startswith(f'ordenes/{o.pk}/'))

    def test_mas_de_cuatro_fotos(self):
        r = self.solicitud(fotos=[imagen(f'{i}.png') for i in range(5)])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Orden.objects.count(), 0)

    def test_foto_invalida_no_crea_la_orden(self):
        r = self.solicitud(fotos=[SimpleUploadedFile('x.png', b'no soy imagen')])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Orden.objects.count(), 0)

    def test_validaciones(self):
        for cambio in ({'servicio': ''}, {'servicio': 9999}, {'modelo': ''}, {'modelo': ' '}, {'problema': ''}, {'problema': 'corto'}):
            r = self.solicitud(**cambio)
            self.assertEqual(r.status_code, 200, cambio)
        self.assertEqual(Orden.objects.count(), 0)

    def test_con_errores_vuelve_al_panel_con_la_ventana_abierta_y_lo_escrito(self):
        r = self.solicitud(problema='corto', modelo='Lenovo X')
        self.assertContains(r, 'Lenovo X')
        self.assertNotContains(r, 'id="modal-solicitud" class="hidden')

    def test_solo_post_y_solo_clientes(self):
        self.assertEqual(self.client.get(reverse('solicitar')).status_code, 405)
        self.client.force_login(self.tec)
        self.solicitud()
        self.client.force_login(self.admin)
        self.solicitud()
        self.assertEqual(Orden.objects.count(), 0)
        self.client.logout()
        self.assertEqual(self.solicitud().status_code, 302)
        self.assertEqual(Orden.objects.count(), 0)


class PanelClienteTests(Base):
    def test_ve_solo_sus_equipos_y_los_entregados_aparte(self):
        mia = self.orden(self.ana)
        ajena = self.orden(self.luis)
        ya = self.orden(self.ana, estado='ENTREGADO', entrega_autorizada=True, entregada_en=timezone.now())
        self.client.force_login(self.ana)
        r = self.client.get(reverse('panel'))
        self.assertEqual(list(r.context['en_proceso']), [mia])
        self.assertEqual(list(r.context['entregados']), [ya])
        self.assertNotIn(ajena, r.context['en_proceso'])

    def test_entregados_sin_fotos_en_la_lista(self):
        ya = self.orden(self.ana, estado='ENTREGADO', entrega_autorizada=True, entregada_en=timezone.now())
        from .models import OrdenFoto
        OrdenFoto.objects.create(orden=ya, imagen=imagen())
        self.client.force_login(self.ana)
        r = self.client.get(reverse('panel'))
        self.assertNotContains(r, '/media/ordenes/')
        self.assertNotContains(self.client.get(reverse('equipo', args=[ya.pk])), '/media/ordenes/')

    def test_atrasada_se_marca(self):
        self.orden(self.ana, fecha_estimada=timezone.localdate() - timedelta(days=2))
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse('panel')), 'Atrasada')

    def test_tecnico_ve_solo_las_asignadas(self):
        mia = self.orden(tecnico=self.tec)
        self.orden(tecnico=self.tec2)
        self.orden()
        self.client.force_login(self.tec)
        self.assertEqual(list(self.client.get(reverse('panel')).context['en_proceso']), [mia])


class DetalleTests(Base):
    def test_cada_quien_ve_solo_lo_suyo(self):
        o = self.orden(self.ana, tecnico=self.tec)
        url = reverse('equipo', args=[o.pk])
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.tec)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.tec2)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_admin_es_llevado_a_su_detalle(self):
        o = self.orden()
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse('equipo', args=[o.pk])), reverse('orden_admin', args=[o.pk]))


class AdminOrdenesTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_solo_admin(self):
        o = self.orden()
        for u in (reverse('ordenes'), reverse('orden_admin', args=[o.pk])):
            self.client.force_login(self.ana)
            self.assertEqual(self.client.get(u).status_code, 302)
            self.client.force_login(self.tec)
            self.assertEqual(self.client.get(u).status_code, 302)
            self.client.logout()
            self.assertEqual(self.client.get(u).status_code, 302)
        self.client.force_login(self.ana)
        self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': self.tec.pk, 'estado': 'DIAGNOSTICO', 'prioridad': 'NORMAL'})
        o.refresh_from_db()
        self.assertEqual((o.tecnico, o.estado), (None, 'RECIBIDO'))

    def test_lista_busqueda_y_filtro(self):
        a = self.orden(self.ana)
        b = self.orden(self.luis, estado='REPARACION', tecnico=self.tec)
        self.assertEqual(len(self.client.get(reverse('ordenes')).context['pagina']), 2)
        self.assertEqual([o.pk for o in self.client.get(reverse('ordenes'), {'q': 'Luis'}).context['pagina']], [b.pk])
        self.assertEqual([o.pk for o in self.client.get(reverse('ordenes'), {'q': f'#{a.pk}'}).context['pagina']], [a.pk])
        self.assertEqual([o.pk for o in self.client.get(reverse('ordenes'), {'estado': 'REPARACION'}).context['pagina']], [b.pk])
        self.assertEqual(len(self.client.get(reverse('ordenes'), {'estado': 'basura'}).context['pagina']), 2)

    def test_asignar_tecnico_y_cambiar_estado_deja_historial(self):
        o = self.orden()
        r = self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': self.tec.pk, 'estado': 'DIAGNOSTICO', 'prioridad': 'ALTA'})
        self.assertRedirects(r, reverse('orden_admin', args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual((o.tecnico, o.estado, o.prioridad), (self.tec, 'DIAGNOSTICO', 'ALTA'))
        eventos = list(o.historial.values_list('evento', flat=True))
        self.assertIn('Asignada a Tec Uno', eventos)
        self.assertIn('Estado: Recibido → En diagnóstico', eventos)
        self.assertIn('Prioridad: Alta', eventos)
        self.assertEqual(o.historial.filter(usuario=self.admin).count(), 3)

    def test_sin_cambios_no_ensucia_el_historial(self):
        o = self.orden()
        self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': '', 'estado': 'RECIBIDO', 'prioridad': 'NORMAL'})
        self.assertEqual(o.historial.count(), 0)

    def test_no_se_avanza_sin_tecnico(self):
        o = self.orden()
        r = self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': '', 'estado': 'REPARACION', 'prioridad': 'NORMAL'})
        self.assertEqual(r.status_code, 200)
        o.refresh_from_db()
        self.assertEqual(o.estado, 'RECIBIDO')

    def test_no_se_puede_marcar_entregado_desde_aqui(self):
        o = self.orden(tecnico=self.tec)
        r = self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': self.tec.pk, 'estado': 'ENTREGADO', 'prioridad': 'NORMAL'})
        self.assertEqual(r.status_code, 200)
        o.refresh_from_db()
        self.assertEqual(o.estado, 'RECIBIDO')

    def test_solo_tecnicos_activos(self):
        self.tec2.is_active = False
        self.tec2.save()
        o = self.orden()
        for quien in (self.tec2, self.ana, self.admin):
            r = self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': quien.pk, 'estado': 'RECIBIDO', 'prioridad': 'NORMAL'})
            self.assertEqual(r.status_code, 200, quien)
        o.refresh_from_db()
        self.assertIsNone(o.tecnico)

    def test_fecha_estimada(self):
        o = self.orden(tecnico=self.tec)
        url = reverse('orden_admin', args=[o.pk])
        base = {'tecnico': self.tec.pk, 'estado': 'RECIBIDO', 'prioridad': 'NORMAL'}
        ayer = timezone.localdate() - timedelta(days=1)
        self.assertEqual(self.client.post(url, {**base, 'fecha_estimada': ayer.isoformat()}).status_code, 200)
        self.assertEqual(self.client.post(url, {**base, 'fecha_estimada': 'no-es-fecha'}).status_code, 200)
        futura = timezone.localdate() + timedelta(days=3)
        self.client.post(url, {**base, 'fecha_estimada': futura.isoformat()})
        o.refresh_from_db()
        self.assertEqual(o.fecha_estimada, futura)
        self.assertTrue(o.historial.filter(evento__startswith='Fecha estimada').exists())

    def test_fecha_pasada_ya_guardada_no_bloquea_otros_cambios(self):
        vieja = timezone.localdate() - timedelta(days=5)
        o = self.orden(tecnico=self.tec, fecha_estimada=vieja)
        r = self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': self.tec.pk, 'estado': 'REPARACION', 'prioridad': 'NORMAL', 'fecha_estimada': vieja.isoformat()})
        self.assertEqual(r.status_code, 302)

    def test_orden_entregada_no_se_modifica(self):
        o = self.orden(tecnico=self.tec, estado='ENTREGADO', entrega_autorizada=True, entregada_en=timezone.now())
        r = self.client.post(reverse('orden_admin', args=[o.pk]), {'tecnico': '', 'estado': 'RECIBIDO', 'prioridad': 'URGENTE'})
        self.assertRedirects(r, reverse('orden_admin', args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual((o.estado, o.tecnico), ('ENTREGADO', self.tec))

    def test_orden_inexistente(self):
        self.assertEqual(self.client.get(reverse('orden_admin', args=[9999])).status_code, 404)
