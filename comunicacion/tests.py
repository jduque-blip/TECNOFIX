from django.test import TestCase
from django.urls import reverse

from ordenes.models import Orden, Servicio
from usuarios.models import Usuario

from .models import Mensaje, Notificacion

CLAVE = 'Taller-2026-ok'


class Base(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin Uno', CLAVE)
        self.cli = Usuario.objects.create_user('c@t.com', 'Cli Uno', CLAVE, rol='CLIENTE', telefono='0991234567')
        self.cli2 = Usuario.objects.create_user('c2@t.com', 'Cli Dos', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@t.com', 'Tec Uno', CLAVE, rol='TECNICO')
        self.tec2 = Usuario.objects.create_user('t2@t.com', 'Tec Dos', CLAVE, rol='TECNICO')
        self.serv = Servicio.objects.create(nombre='Computadoras', precio_base=10)
        self.orden = Orden.objects.create(cliente=self.cli, servicio=self.serv, modelo='HP 15', problema='No enciende para nada')

    def entrar(self, u):
        self.client.force_login(u)


class NotificacionesTests(Base):
    def test_nueva_solicitud_avisa_al_admin(self):
        self.entrar(self.cli)
        self.client.post(reverse('solicitar'), {'servicio': self.serv.pk, 'modelo': 'Xbox', 'problema': 'No lee los discos nunca'})
        n = Notificacion.objects.get(usuario=self.admin)
        self.assertIn('Nueva solicitud', n.texto)
        self.assertEqual(Notificacion.objects.exclude(usuario=self.admin).count(), 0)

    def test_asignar_tecnico_avisa_a_tecnico_y_cliente(self):
        self.entrar(self.admin)
        self.client.post(reverse('orden_admin', args=[self.orden.pk]), {'tecnico': self.tec.pk, 'estado': 'DIAGNOSTICO', 'prioridad': 'NORMAL', 'fecha_estimada': '2099-01-01'})
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='asignó').exists())
        textos = ' '.join(Notificacion.objects.filter(usuario=self.cli).values_list('texto', flat=True))
        self.assertIn('ya tiene técnico', textos)
        self.assertIn('ahora está', textos)
        self.assertIn('Entrega estimada', textos)

    def test_cambio_sin_novedad_no_avisa(self):
        self.entrar(self.admin)
        self.client.post(reverse('orden_admin', args=[self.orden.pk]), {'tecnico': '', 'estado': 'RECIBIDO', 'prioridad': 'NORMAL', 'fecha_estimada': ''})
        self.assertEqual(Notificacion.objects.count(), 0)

    def test_cambio_de_tecnico_avisa_al_anterior(self):
        self.orden.tecnico = self.tec
        self.orden.save()
        self.entrar(self.admin)
        self.client.post(reverse('orden_admin', args=[self.orden.pk]), {'tecnico': self.tec2.pk, 'estado': 'RECIBIDO', 'prioridad': 'NORMAL', 'fecha_estimada': ''})
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec, texto__contains='Ya no tienes').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.tec2, texto__contains='asignó').exists())

    def test_cliente_corrige_y_elimina_avisa_al_admin(self):
        self.entrar(self.cli)
        self.client.post(reverse('equipo_editar', args=[self.orden.pk]), {'servicio': self.serv.pk, 'modelo': 'HP 16', 'problema': 'No enciende para nada'})
        self.client.post(reverse('equipo_eliminar', args=[self.orden.pk]))
        textos = list(Notificacion.objects.filter(usuario=self.admin).values_list('texto', flat=True))
        self.assertTrue(any('corrigió' in t for t in textos) and any('eliminó' in t for t in textos))

    def test_cada_quien_ve_solo_las_suyas(self):
        Notificacion.avisar(self.cli, 'Para el cliente')
        Notificacion.avisar(self.tec, 'Para el técnico')
        self.entrar(self.cli)
        r = self.client.get(reverse('notificaciones'))
        self.assertContains(r, 'Para el cliente')
        self.assertNotContains(r, 'Para el técnico')

    def test_abrir_marca_leida_y_redirige(self):
        n = Notificacion.avisar(self.cli, 'Hola', f'/equipos/{self.orden.pk}/')
        self.entrar(self.cli)
        r = self.client.get(reverse('notificacion_abrir', args=[n.pk]))
        self.assertRedirects(r, f'/equipos/{self.orden.pk}/')
        n.refresh_from_db()
        self.assertTrue(n.leida)

    def test_no_abre_notificacion_ajena_ni_enlace_externo(self):
        ajena = Notificacion.avisar(self.tec, 'x')
        self.entrar(self.cli)
        self.assertEqual(self.client.get(reverse('notificacion_abrir', args=[ajena.pk])).status_code, 404)
        externa = Notificacion.avisar(self.cli, 'x', 'https://malo.com/')
        self.assertRedirects(self.client.get(reverse('notificacion_abrir', args=[externa.pk])), reverse('notificaciones'))

    def test_marcar_todas_y_contador(self):
        Notificacion.avisar(self.cli, 'a'); Notificacion.avisar(self.cli, 'b')
        self.entrar(self.cli)
        self.assertEqual(self.client.get(reverse('panel')).context['n_notif'], 2)
        self.client.post(reverse('notificaciones_leer'))
        self.assertEqual(self.client.get(reverse('panel')).context['n_notif'], 0)

    def test_exige_ingreso(self):
        self.assertEqual(self.client.get(reverse('notificaciones')).status_code, 302)


class ChatTests(Base):
    def enviar(self, a, b, texto='Hola', **extra):
        self.entrar(a)
        return self.client.post(reverse('chat', args=[b.pk]), {'texto': texto, **extra})

    def test_cliente_escribe_a_administracion(self):
        r = self.enviar(self.cli, self.admin, 'Hola admin')
        self.assertEqual(r.status_code, 302)
        m = Mensaje.objects.get()
        self.assertEqual((m.de, m.para, m.texto), (self.cli, self.admin, 'Hola admin'))

    def test_cliente_y_tecnico_solo_si_comparten_una_orden(self):
        self.enviar(self.cli, self.tec)
        self.assertEqual(Mensaje.objects.count(), 0)
        self.orden.tecnico = self.tec
        self.orden.save()
        self.enviar(self.cli, self.tec)
        self.assertEqual(Mensaje.objects.count(), 1)
        self.enviar(self.tec, self.cli)
        self.assertEqual(Mensaje.objects.count(), 2)

    def test_no_entre_clientes_ni_con_tecnico_ajeno_ni_admin_con_admin(self):
        self.enviar(self.cli, self.cli2)
        self.orden.tecnico = self.tec
        self.orden.save()
        self.enviar(self.cli2, self.tec)
        otro = Usuario.objects.create_superuser('b@t.com', 'Admin Dos', CLAVE)
        self.enviar(self.admin, otro)
        self.assertEqual(Mensaje.objects.count(), 0)

    def test_no_escribe_a_cuenta_desactivada(self):
        self.cli.is_active = False
        self.cli.save()
        self.enviar(self.admin, self.cli)
        self.assertEqual(Mensaje.objects.count(), 0)

    def test_validaciones_del_mensaje(self):
        self.assertEqual(self.enviar(self.cli, self.admin, '   ').status_code, 200)
        self.assertEqual(self.enviar(self.cli, self.admin, 'x' * 1001).status_code, 200)
        self.assertEqual(Mensaje.objects.count(), 0)

    def test_el_texto_se_escapa(self):
        self.enviar(self.cli, self.admin, '<script>alert(1)</script>')
        self.entrar(self.admin)
        r = self.client.get(reverse('chat', args=[self.cli.pk]))
        self.assertNotContains(r, '<script>alert(1)</script>')
        self.assertContains(r, '&lt;script&gt;')

    def test_leer_marca_como_leido_y_baja_el_contador(self):
        self.enviar(self.cli, self.admin, 'Uno')
        self.entrar(self.admin)
        self.assertEqual(self.client.get(reverse('panel')).context['n_chat'], 1)
        self.client.get(reverse('chat', args=[self.cli.pk]))
        self.assertEqual(self.client.get(reverse('panel')).context['n_chat'], 0)

    def test_no_lee_conversaciones_ajenas(self):
        self.enviar(self.cli, self.admin, 'Privado')
        self.entrar(self.cli2)
        r = self.client.get(reverse('chat_bandeja'))
        self.assertNotContains(r, 'Privado')
        self.assertEqual(self.client.get(reverse('chat_nuevos', args=[self.cli.pk])).status_code, 404)

    def test_nuevos_devuelve_solo_los_posteriores(self):
        self.enviar(self.cli, self.admin, 'Uno')
        self.enviar(self.cli, self.admin, 'Dos')
        primero = Mensaje.objects.order_by('pk').first()
        self.entrar(self.admin)
        r = self.client.get(reverse('chat_nuevos', args=[self.cli.pk]), {'despues': primero.pk})
        self.assertEqual([m['texto'] for m in r.json()['mensajes']], ['Dos'])

    def test_orden_vinculada_solo_si_corresponde(self):
        self.enviar(self.cli, self.admin, 'Sobre mi equipo', orden=self.orden.pk)
        self.assertEqual(Mensaje.objects.get().orden, self.orden)
        Mensaje.objects.all().delete()
        self.enviar(self.cli2, self.admin, 'Equipo ajeno', orden=self.orden.pk)
        self.assertIsNone(Mensaje.objects.get().orden)

    def test_bandeja_lista_conversaciones_y_contactos(self):
        self.orden.tecnico = self.tec
        self.orden.save()
        self.enviar(self.cli, self.admin, 'Hola')
        self.entrar(self.cli)
        r = self.client.get(reverse('chat_bandeja'))
        self.assertContains(r, 'Admin Uno')
        self.assertContains(r, 'Tec Uno')   # contacto nuevo por su orden
        self.assertNotContains(r, 'Cli Dos')

    def test_boton_contactar_en_el_detalle(self):
        self.entrar(self.cli)
        r = self.client.get(reverse('equipo', args=[self.orden.pk]))
        self.assertContains(r, f"{reverse('chat', args=[self.admin.pk])}?orden={self.orden.pk}")  # sin técnico: administración
        self.orden.tecnico = self.tec
        self.orden.save()
        r = self.client.get(reverse('equipo', args=[self.orden.pk]))
        self.assertContains(r, f"{reverse('chat', args=[self.tec.pk])}?orden={self.orden.pk}")
        self.entrar(self.tec)
        r = self.client.get(reverse('equipo', args=[self.orden.pk]))
        self.assertContains(r, f"{reverse('chat', args=[self.cli.pk])}?orden={self.orden.pk}")

    def test_admin_ve_contactar_cliente_y_tecnico(self):
        self.orden.tecnico = self.tec
        self.orden.save()
        self.entrar(self.admin)
        r = self.client.get(reverse('orden_admin', args=[self.orden.pk]))
        self.assertContains(r, 'Contactar al cliente')
        self.assertContains(r, 'Contactar al técnico')
        self.assertContains(r, 'wa.me/593991234567')

    def test_exige_ingreso(self):
        self.assertEqual(self.client.get(reverse('chat_bandeja')).status_code, 302)
        self.assertEqual(self.client.post(reverse('chat', args=[self.admin.pk]), {'texto': 'x'}).status_code, 302)
