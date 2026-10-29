from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from comunicacion.models import Notificacion

from .models import Usuario


class OlvideContrasenaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_user(email='admin@t.com', password='x-Clave-123', nombre='Admin', rol=Usuario.Rol.ADMIN)
        self.cliente = Usuario.objects.create_user(email='ana@t.com', password='x-Clave-123', nombre='Ana', rol=Usuario.Rol.CLIENTE)
        self.tecnico = Usuario.objects.create_user(email='tec@t.com', password='x-Clave-123', nombre='Tec', rol=Usuario.Rol.TECNICO)

    def pedir(self, email):
        return self.client.post(reverse('olvide_contrasena'), {'email': email})

    def test_cliente_avisa_al_admin_con_enlace_a_restablecer(self):
        r = self.pedir('ana@t.com')
        self.assertRedirects(r, reverse('inicio'))
        n = Notificacion.objects.get(usuario=self.admin)
        self.assertIn('Ana', n.texto)
        self.assertEqual(n.enlace, reverse('usuario_contrasena', args=[self.cliente.pk]))

    def test_tecnico_tambien_puede_pedirlo(self):
        self.pedir('TEC@t.com')
        self.assertEqual(Notificacion.objects.filter(usuario=self.admin).count(), 1)

    def test_respuesta_igual_si_el_correo_no_existe(self):
        r1 = self.pedir('ana@t.com')
        r2 = self.pedir('nadie@t.com')
        self.assertEqual(r1.status_code, r2.status_code)
        self.assertEqual(Notificacion.objects.count(), 1)

    def test_no_repite_el_aviso_seguido(self):
        self.pedir('ana@t.com')
        self.pedir('ana@t.com')
        self.assertEqual(Notificacion.objects.filter(usuario=self.admin).count(), 1)

    def test_administrador_y_cuentas_inactivas_no_generan_aviso(self):
        self.pedir('admin@t.com')
        Usuario.objects.filter(pk=self.cliente.pk).update(is_active=False)
        self.pedir('ana@t.com')
        self.assertEqual(Notificacion.objects.count(), 0)

    def test_limite_por_equipo(self):
        for i in range(12):
            Usuario.objects.create_user(email=f'u{i}@t.com', password='x-Clave-123', nombre=f'U{i}', rol=Usuario.Rol.CLIENTE)
            self.pedir(f'u{i}@t.com')
        self.assertEqual(Notificacion.objects.count(), 10)

    def test_correo_vacio_reabre_la_ventana_con_error(self):
        r = self.pedir('')
        self.assertContains(r, 'Escribe tu correo.')

    def test_el_enlace_aparece_en_el_login(self):
        self.assertContains(self.client.get(reverse('inicio')), 'Olvidaste tu contraseña')

    def test_flujo_completo_admin_restablece_y_usuario_cambia(self):
        self.pedir('ana@t.com')
        self.client.force_login(self.admin)
        r = self.client.post(reverse('usuario_contrasena', args=[self.cliente.pk]), {'password': 'nueva1'})
        self.assertEqual(r.status_code, 302)
        self.client.logout()
        self.client.login(username='ana@t.com', password='nueva1')
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.debe_cambiar_contrasena)
