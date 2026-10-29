import shutil
import tempfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ordenes.tests import imagen
from usuarios.models import Usuario

from .imagenes import PORTADA
from .models import Portada

CLAVE = 'Taller-2026-ok'


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='tf_media_'))
class PortadaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = Usuario.objects.create_superuser('admin@correo.com', 'Admin Uno', CLAVE)
        self.cliente = Usuario.objects.create_user('c@correo.com', 'Cli Uno', CLAVE, rol='CLIENTE')

    def test_sin_foto_usa_la_de_ejemplo(self):
        self.assertContains(self.client.get(reverse('inicio')), PORTADA.split('?')[0])

    def test_admin_sube_y_la_portada_la_muestra(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('portada_foto'), {'foto': imagen()})
        p = Portada.actual()
        self.assertTrue(p.foto.name.startswith('portada/'))
        self.assertContains(self.client.get(reverse('inicio')), p.foto.url)

    def test_cambiar_borra_la_anterior_y_quitar_vuelve_al_ejemplo(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('portada_foto'), {'foto': imagen()})
        primera = Portada.actual().foto.name
        self.client.post(reverse('portada_foto'), {'foto': imagen('b.png')})
        actual = Portada.actual()
        self.assertFalse(actual.foto.storage.exists(primera))
        self.client.post(reverse('portada_quitar'))
        self.assertEqual(Portada.actual().foto_url, PORTADA)
        self.assertFalse(actual.foto.storage.exists(actual.foto.name))

    def test_archivo_invalido(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('portada_foto'), {'foto': SimpleUploadedFile('x.png', b'no')})
        self.assertFalse(Portada.actual().foto)

    def test_solo_admin(self):
        self.client.force_login(self.cliente)
        self.client.post(reverse('portada_foto'), {'foto': imagen()})
        self.assertFalse(Portada.actual().foto)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('portada_foto'), {'foto': imagen()}).status_code, 302)
        self.assertFalse(Portada.actual().foto)
