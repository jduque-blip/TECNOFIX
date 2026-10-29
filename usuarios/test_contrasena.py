from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from . import claves
from .models import Usuario

CLAVE = 'Taller-2026-ok'
NUEVA = 'Otra-Clave-2026'


class CambioContrasenaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = Usuario.objects.create_user('ana@correo.com', 'Ana Pérez', CLAVE, rol='CLIENTE')
        self.admin = Usuario.objects.create_superuser('admin@correo.com', 'Admin Uno', CLAVE)
        self.client.force_login(self.u)

    def cambiar(self, actual=CLAVE, nueva=NUEVA, repetir=None):
        return self.client.post(reverse('cambiar_contrasena'),
                                {'actual': actual, 'nueva': nueva, 'repetir': repetir or nueva})

    def test_exige_ingresar(self):
        self.client.logout()
        r = self.client.get(reverse('cambiar_contrasena'))
        self.assertEqual(r.status_code, 302)

    def test_cambio_correcto_y_la_sesion_se_mantiene(self):
        r = self.cambiar()
        self.assertEqual(r.status_code, 302)
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password(NUEVA))
        self.assertEqual(self.client.get(reverse('panel')).status_code, 200)

    def test_actual_incorrecta(self):
        r = self.cambiar(actual='Equivocada-123')
        self.assertEqual(r.status_code, 200)
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password(CLAVE))

    def test_no_coinciden(self):
        r = self.cambiar(repetir='Distinta-2026-x')
        self.assertEqual(r.status_code, 200)
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password(CLAVE))

    def test_nueva_igual_a_actual(self):
        r = self.cambiar(nueva=CLAVE)
        self.assertEqual(r.status_code, 200)

    def test_validadores(self):
        for mala in ('12345678', 'corta1', 'password'):
            r = self.cambiar(nueva=mala)
            self.assertEqual(r.status_code, 200, mala)
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password(CLAVE))

    def test_bloqueo_tras_intentos_fallidos(self):
        for _ in range(6):
            self.cambiar(actual='Equivocada-123')
        r = self.cambiar()  # ahora con la clave correcta
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password(CLAVE))
        self.assertEqual(r.status_code, 200)


class CambioObligatorioTests(TestCase):
    def setUp(self):
        self.u = Usuario.objects.create_user('t@correo.com', 'Tec Uno', CLAVE, rol='TECNICO',
                                             debe_cambiar_contrasena=True)
        self.client.force_login(self.u)

    def test_todo_redirige_a_cambiar(self):
        for nombre in ('panel', 'inicio'):
            r = self.client.get(reverse(nombre))
            self.assertRedirects(r, reverse('cambiar_contrasena'), fetch_redirect_response=False)

    def test_puede_ver_la_pantalla_y_salir(self):
        self.assertEqual(self.client.get(reverse('cambiar_contrasena')).status_code, 200)
        self.assertEqual(self.client.post(reverse('salir')).status_code, 302)

    def test_al_cambiar_queda_libre(self):
        self.client.post(reverse('cambiar_contrasena'), {'actual': CLAVE, 'nueva': NUEVA, 'repetir': NUEVA})
        self.u.refresh_from_db()
        self.assertFalse(self.u.debe_cambiar_contrasena)
        self.assertEqual(self.client.get(reverse('panel')).status_code, 200)


class RestablecerTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('admin@correo.com', 'Admin Uno', CLAVE)
        self.t = Usuario.objects.create_user('t@correo.com', 'Tec Uno', CLAVE, rol='TECNICO')

    def url(self, u=None):
        return reverse('usuario_contrasena', args=[(u or self.t).pk])

    def test_admin_restablece_y_obliga_a_cambiar(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url(), {'password': 'k7mp4x'})
        self.assertRedirects(r, reverse('usuario_credencial', args=[self.t.pk]), fetch_redirect_response=False)
        self.t.refresh_from_db()
        self.assertTrue(self.t.check_password('k7mp4x'))
        self.assertTrue(self.t.debe_cambiar_contrasena)

    def test_la_clave_temporal_corta_se_acepta(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), {'password': 'ab12'}).status_code, 302)
        self.t.refresh_from_db()
        self.assertTrue(self.t.check_password('ab12'))

    def test_clave_muy_corta_se_rechaza(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url(), {'password': 'abc'})
        self.assertEqual(r.status_code, 200)
        self.t.refresh_from_db()
        self.assertTrue(self.t.check_password(CLAVE))

    def test_el_formulario_ya_trae_una_clave_generada(self):
        self.client.force_login(self.admin)
        clave = self.client.get(self.url()).context['clave_inicial']
        self.assertEqual(len(clave), 6)
        self.assertTrue(set(clave) <= set(claves.ALFABETO))

    def test_credencial_con_enlace_de_whatsapp(self):
        self.t.telefono = '0987654321'
        self.t.save()
        self.client.force_login(self.admin)
        r = self.client.post(self.url(), {'password': 'k7mp4x'}, follow=True)
        self.assertContains(r, 'k7mp4x')
        self.assertContains(r, 'https://wa.me/593987654321?text=')

    def test_sin_telefono_no_hay_whatsapp_pero_se_puede_agregar(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url(), {'password': 'k7mp4x'}, follow=True)
        self.assertNotContains(r, 'wa.me')
        self.client.post(self.url(), {'password': 'k7mp4x', 'telefono': '0991112223'})
        self.t.refresh_from_db()
        self.assertEqual(self.t.telefono, '0991112223')

    def test_telefono_invalido_en_restablecer(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), {'password': 'k7mp4x', 'telefono': '123'}).status_code, 200)

    def test_generador_y_numero(self):
        self.assertEqual(len({claves.generar_clave() for _ in range(50)}) > 40, True)
        self.assertEqual(claves.numero_whatsapp('0991234567'), '593991234567')
        self.assertEqual(claves.numero_whatsapp('042123456'), '')

    def test_no_aplica_a_administradores(self):
        self.client.force_login(self.admin)
        otro = Usuario.objects.create_superuser('otro@correo.com', 'Otro Admin', CLAVE)
        r = self.client.post(self.url(otro), {'password': 'k7mp4x'})
        self.assertEqual(r.status_code, 404)

    def test_solo_admin(self):
        self.client.force_login(self.t)
        self.assertIn(self.client.get(self.url()).status_code, (302, 403, 404))
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
