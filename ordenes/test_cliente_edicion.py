import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from usuarios.models import Usuario

from .models import Orden, OrdenFoto, Servicio
from .tests import CLAVE, imagen

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class EdicionClienteTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.cli = Usuario.objects.create_user('c@correo.com', 'Cli Uno', CLAVE, rol='CLIENTE')
        self.otro = Usuario.objects.create_user('o@correo.com', 'Otro Cli', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@correo.com', 'Tec Uno', CLAVE, rol='TECNICO')
        self.s1 = Servicio.objects.create(nombre='Computadoras', precio_base=10)
        self.s2 = Servicio.objects.create(nombre='Consolas', precio_base=10)
        self.orden = Orden.objects.create(cliente=self.cli, servicio=self.s1, modelo='HP 15', problema='No enciende para nada')
        self.client.force_login(self.cli)

    def datos(self, **c):
        return {'servicio': self.s2.pk, 'modelo': 'Xbox One', 'problema': 'Se apaga al jugar un rato', **c}

    def editar(self, **c):
        return self.client.post(reverse('equipo_editar', args=[self.orden.pk]), self.datos(**c))

    def test_edita_datos_y_queda_en_el_historial(self):
        r = self.editar()
        self.assertRedirects(r, reverse('equipo', args=[self.orden.pk]))
        self.orden.refresh_from_db()
        self.assertEqual((self.orden.modelo, self.orden.servicio), ('Xbox One', self.s2))
        self.assertTrue(self.orden.historial.filter(evento__contains='corregida').exists())

    def test_se_pueden_ir_sumando_fotos(self):
        self.editar(fotos=[imagen('a.png'), imagen('b.png')])
        self.assertEqual(self.orden.fotos.count(), 2)
        self.editar(fotos=imagen('c.png'))
        self.assertEqual(self.orden.fotos.count(), 3)

    def test_quitar_foto_borra_el_archivo(self):
        self.editar(fotos=[imagen('a.png'), imagen('b.png')])
        f = self.orden.fotos.first()
        ruta = f.imagen.path
        self.editar(quitar=[f.pk])
        self.assertEqual(self.orden.fotos.count(), 1)
        import os
        self.assertFalse(os.path.exists(ruta))

    def test_maximo_cuatro_fotos_en_total(self):
        self.editar(fotos=[imagen(f'{i}.png') for i in range(3)])
        r = self.editar(fotos=[imagen('x.png'), imagen('y.png')])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'máximo 4')
        self.assertEqual(self.orden.fotos.count(), 3)

    def test_validaciones(self):
        self.assertEqual(self.editar(modelo='').status_code, 200)
        self.assertEqual(self.editar(problema='corto').status_code, 200)

    def test_elimina_su_solicitud_y_sus_fotos(self):
        self.editar(fotos=imagen('a.png'))
        r = self.client.post(reverse('equipo_eliminar', args=[self.orden.pk]))
        self.assertRedirects(r, reverse('panel'))
        self.assertFalse(Orden.objects.filter(pk=self.orden.pk).exists())
        self.assertEqual(OrdenFoto.objects.count(), 0)

    def test_con_tecnico_ya_no_se_modifica_ni_elimina(self):
        self.orden.tecnico = self.tec
        self.orden.save()
        self.assertRedirects(self.editar(), reverse('equipo', args=[self.orden.pk]))
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.modelo, 'HP 15')
        self.client.post(reverse('equipo_eliminar', args=[self.orden.pk]))
        self.assertTrue(Orden.objects.filter(pk=self.orden.pk).exists())
        self.assertEqual(self.client.get(reverse('equipo_editar', args=[self.orden.pk])).status_code, 302)

    def test_los_botones_solo_aparecen_sin_tecnico(self):
        url = reverse('equipo', args=[self.orden.pk])
        self.assertContains(self.client.get(url), reverse('equipo_editar', args=[self.orden.pk]))
        self.orden.tecnico = self.tec
        self.orden.save()
        r = self.client.get(url)
        self.assertNotContains(r, reverse('equipo_editar', args=[self.orden.pk]))
        self.assertContains(r, 'Ya tiene técnico asignado')

    def test_no_toca_lo_de_otro_cliente_ni_otros_roles(self):
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(reverse('equipo_editar', args=[self.orden.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('equipo_eliminar', args=[self.orden.pk])).status_code, 404)
        self.client.force_login(self.tec)
        self.assertEqual(self.client.post(reverse('equipo_eliminar', args=[self.orden.pk])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('equipo_eliminar', args=[self.orden.pk])).status_code, 302)
        self.assertTrue(Orden.objects.filter(pk=self.orden.pk).exists())

    def test_eliminar_exige_post(self):
        self.assertEqual(self.client.get(reverse('equipo_eliminar', args=[self.orden.pk])).status_code, 405)
