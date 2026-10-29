import io
import shutil
import tempfile
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from django.urls import reverse

from core.models import Configuracion
from usuarios.models import Usuario

from .models import Orden, Servicio, Tarifa

CLAVE = 'Taller-2026-ok'


def imagen(nombre='foto.png', formato='PNG', tam=(40, 30)):
    """Archivo de imagen real y diminuto para las pruebas de subida."""
    buf = io.BytesIO()
    Image.new('RGB', tam, (200, 80, 40)).save(buf, formato)
    return SimpleUploadedFile(nombre, buf.getvalue())


class TarifasBase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('admin@correo.com', 'Admin Uno', CLAVE)
        self.cliente = Usuario.objects.create_user('c@correo.com', 'Cli Uno', CLAVE, rol='CLIENTE')
        self.client.force_login(self.admin)

    def mensajes(self, r):
        return [str(m) for m in get_messages(r.wsgi_request)]


class PermisosTarifas(TarifasBase):
    def test_solo_el_admin_entra_y_actua(self):
        t = Tarifa.objects.create(nombre='Limpieza', precio=10)
        urls = [reverse('tarifas')]
        posts = [reverse('iva_guardar'), reverse('tarifa_precio', args=[t.pk]), reverse('tarifa_eliminar', args=[t.pk])]
        self.client.force_login(self.cliente)
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 302)
        for u in posts:
            self.assertEqual(self.client.post(u, {'valor': '5', 'precio': '1'}).status_code, 302)
        self.assertTrue(Tarifa.objects.filter(pk=t.pk, precio=10).exists())
        self.assertNotEqual(Configuracion.iva(), Decimal('5'))
        self.client.logout()
        self.assertEqual(self.client.get(urls[0]).status_code, 302)

    def test_acciones_solo_post(self):
        t = Tarifa.objects.create(nombre='Limpieza', precio=10)
        self.assertEqual(self.client.get(reverse('tarifa_eliminar', args=[t.pk])).status_code, 405)

    def test_pestana_visible(self):
        self.assertContains(self.client.get(reverse('tarifas')), 'Impuesto (IVA)')


class IvaTests(TarifasBase):
    def test_guardar_iva(self):
        self.client.post(reverse('iva_guardar'), {'valor': '12.5'})
        self.assertEqual(Configuracion.iva(), Decimal('12.5'))

    def test_iva_cero_es_valido(self):
        self.client.post(reverse('iva_guardar'), {'valor': '0'})
        self.assertEqual(Configuracion.iva(), Decimal('0'))

    def test_iva_invalido(self):
        Configuracion.iva()
        for malo in ('-1', '101', 'abc', ''):
            r = self.client.post(reverse('iva_guardar'), {'valor': malo})
            self.assertTrue(self.mensajes(r), malo)
        self.assertEqual(Configuracion.iva(), Decimal('15'))


class TarifaTests(TarifasBase):
    def alta(self, **c):
        return self.client.post(reverse('tarifas'), {'accion': 'tarifa', 'nombre': 'Mano de obra', 'precio': '20', **c})

    def test_alta(self):
        self.assertRedirects(self.alta(), reverse('tarifas'))
        self.assertEqual(Tarifa.objects.get().precio, Decimal('20'))

    def test_validaciones(self):
        for datos in ({'precio': '0'}, {'precio': '-3'}, {'precio': 'x'}, {'precio': ''}, {'nombre': ''}, {'nombre': 'ab'}, {'precio': '1.234'}):
            r = self.alta(**datos)
            self.assertEqual(r.status_code, 200, datos)
        self.assertEqual(Tarifa.objects.count(), 0)

    def test_nombre_repetido_sin_distinguir_mayusculas(self):
        self.alta()
        r = self.alta(nombre='mano de OBRA')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Tarifa.objects.count(), 1)

    def test_cambiar_precio(self):
        t = Tarifa.objects.create(nombre='Limpieza', precio=10)
        self.client.post(reverse('tarifa_precio', args=[t.pk]), {'precio': '12.75'})
        t.refresh_from_db()
        self.assertEqual(t.precio, Decimal('12.75'))

    def test_precio_invalido_no_cambia(self):
        t = Tarifa.objects.create(nombre='Limpieza', precio=10)
        for malo in ('0', '-1', 'abc', ''):
            self.client.post(reverse('tarifa_precio', args=[t.pk]), {'precio': malo})
        t.refresh_from_db()
        self.assertEqual(t.precio, Decimal('10'))

    def test_eliminar(self):
        t = Tarifa.objects.create(nombre='Limpieza', precio=10)
        self.client.post(reverse('tarifa_eliminar', args=[t.pk]))
        self.assertFalse(Tarifa.objects.exists())

    def test_la_pantalla_muestra_numeros_con_punto(self):
        Tarifa.objects.create(nombre='Limpieza', precio=Decimal('12.50'))
        self.assertContains(self.client.get(reverse('tarifas')), 'value="12.5"')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='tf_media_'))
class TarifasPorServicioTests(TarifasBase):
    def setUp(self):
        super().setUp()
        self.compu = Servicio.objects.create(nombre='Computadoras', precio_base=40)
        self.consola = Servicio.objects.create(nombre='Consolas', precio_base=50)

    def alta(self, servicio, **c):
        datos = {'accion': 'tarifa', 'servicio': servicio.pk if servicio else '', 'nombre': 'Diagnóstico', 'precio': '5', **c}
        return self.client.post(reverse('tarifas'), datos)

    def test_la_tarifa_queda_en_su_servicio(self):
        self.assertRedirects(self.alta(self.compu), reverse('tarifas'))
        t = Tarifa.objects.get()
        self.assertEqual(t.servicio, self.compu)
        self.assertEqual(list(self.compu.tarifas.all()), [t])
        self.assertEqual(self.consola.tarifas.count(), 0)

    def test_el_mismo_nombre_en_otro_servicio_si_se_permite(self):
        self.alta(self.compu)
        self.alta(self.consola)
        self.alta(None)
        self.assertEqual(Tarifa.objects.count(), 3)

    def test_nombre_repetido_en_el_mismo_servicio_no(self):
        self.alta(self.compu)
        self.assertEqual(self.alta(self.compu, nombre='diagnóstico').status_code, 200)
        self.assertEqual(self.alta(None).status_code, 302)
        self.assertEqual(self.alta(None, nombre='DIAGNÓSTICO').status_code, 200)
        self.assertEqual(Tarifa.objects.count(), 2)

    def test_servicio_que_no_existe_o_invalido(self):
        self.assertEqual(self.client.post(reverse('tarifas'), {'accion': 'tarifa', 'servicio': '9999', 'nombre': 'Algo', 'precio': '1'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('tarifas'), {'accion': 'tarifa', 'servicio': 'x', 'nombre': 'Algo', 'precio': '1'}).status_code, 404)

    def test_el_error_aparece_solo_en_la_tarjeta_del_servicio(self):
        r = self.alta(self.compu, precio='0')
        self.assertEqual(r.status_code, 200)
        grupos = {g['servicio'].pk if g['servicio'] else None: g for g in r.context['grupos']}
        self.assertTrue(grupos[self.compu.pk]['form'].errors)
        self.assertFalse(grupos[self.consola.pk]['form'].errors)
        self.assertFalse(grupos[None]['form'].errors)

    def test_la_pantalla_agrupa_por_servicio_y_deja_la_general_al_final(self):
        self.alta(self.compu)
        self.alta(None, nombre='Mano de obra')
        r = self.client.get(reverse('tarifas'))
        grupos = r.context['grupos']
        self.assertEqual([g['servicio'] for g in grupos], [*Servicio.objects.all(), None])
        self.assertContains(r, 'Tarifas generales')

    def test_eliminar_un_servicio_elimina_sus_tarifas(self):
        self.alta(self.compu)
        self.client.post(reverse('servicio_eliminar', args=[self.compu.pk]))
        self.assertFalse(Tarifa.objects.exists())

    def test_la_portada_muestra_las_tarifas_del_servicio_y_las_generales(self):
        Tarifa.objects.create(servicio=self.compu, nombre='Cambio de pieza', precio=20)
        Tarifa.objects.create(servicio=self.consola, nombre='Pasta térmica', precio=25)
        Tarifa.objects.create(nombre='Mano de obra', precio=15)
        html = self.client.get(reverse('inicio')).content.decode()
        modal = html.split(f'id="modal-servicio-{self.compu.pk}"')[1].split('data-modal')[0] if False else html.split(f'id="modal-servicio-{self.compu.pk}"')[1].split('id="modal-servicio-')[0]
        self.assertIn('Cambio de pieza', modal)
        self.assertIn('Mano de obra', modal)
        self.assertNotIn('Pasta térmica', modal)


class ServicioTests(TarifasBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def alta(self, **c):
        datos = {'accion': 'servicio', 'nombre': 'Televisores', 'precio_base': '30', **c}
        return self.client.post(reverse('tarifas'), datos)

    def test_alta_sin_foto(self):
        r = self.alta()
        self.assertRedirects(r, f"{reverse('tarifas')}#servicio-{Servicio.objects.get().pk}", fetch_redirect_response=False)
        s = Servicio.objects.get()
        self.assertEqual((bool(s.foto), s.precio_base), (False, Decimal('30')))

    def test_alta_con_foto_propia(self):
        self.alta(foto=imagen())
        s = Servicio.objects.get()
        self.assertTrue(s.foto.name.startswith('servicios/'))
        self.assertTrue(s.foto.name.endswith('.png'))
        self.assertEqual(s.foto_url, s.foto.url)

    def test_el_archivo_no_conserva_el_nombre_del_usuario(self):
        self.alta(foto=imagen('../../raro nombre.png'))
        self.assertNotIn('raro', Servicio.objects.get().foto.name)

    def test_archivo_que_no_es_imagen(self):
        falso = SimpleUploadedFile('virus.png', b'esto no es una imagen')
        self.assertEqual(self.alta(foto=falso).status_code, 200)
        self.assertEqual(Servicio.objects.count(), 0)

    def test_formato_no_permitido(self):
        self.assertEqual(self.alta(foto=imagen('a.gif', 'GIF')).status_code, 200)
        self.assertEqual(Servicio.objects.count(), 0)

    def test_foto_demasiado_pesada(self):
        grande = SimpleUploadedFile('g.png', imagen().read() + b'0' * (3 * 1024 * 1024 + 1))
        self.assertEqual(self.alta(foto=grande).status_code, 200)
        self.assertEqual(Servicio.objects.count(), 0)

    def test_validaciones(self):
        for datos in ({'precio_base': '0'}, {'precio_base': 'x'}, {'nombre': ''}, {'nombre': 'ab'}):
            self.assertEqual(self.alta(**datos).status_code, 200, datos)
        self.assertEqual(Servicio.objects.count(), 0)

    def test_nombre_repetido(self):
        self.alta()
        self.assertEqual(self.alta(nombre='televisores').status_code, 200)
        self.assertEqual(Servicio.objects.count(), 1)

    def test_subir_y_cambiar_foto_borra_la_anterior(self):
        s = Servicio.objects.create(nombre='Laptops', precio_base=40)
        self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': imagen()})
        s.refresh_from_db()
        primera = s.foto.name
        self.assertTrue(primera)
        self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': imagen('otra.png')})
        s.refresh_from_db()
        self.assertNotEqual(s.foto.name, primera)
        self.assertFalse(s.foto.storage.exists(primera))
        self.assertTrue(s.foto.storage.exists(s.foto.name))

    def test_foto_invalida_no_cambia_la_actual(self):
        s = Servicio.objects.create(nombre='Laptops', precio_base=40)
        self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': imagen()})
        s.refresh_from_db()
        actual = s.foto.name
        r = self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': SimpleUploadedFile('x.png', b'no')})
        self.assertTrue(self.mensajes(r))
        s.refresh_from_db()
        self.assertEqual(s.foto.name, actual)

    def test_quitar_foto_vuelve_a_la_de_ejemplo(self):
        s = Servicio.objects.create(nombre='Laptops', precio_base=40, imagen='https://ejemplo.com/a.jpg')
        self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': imagen()})
        s.refresh_from_db()
        archivo = s.foto.name
        self.client.post(reverse('servicio_foto_quitar', args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.foto_url, 'https://ejemplo.com/a.jpg')
        self.assertFalse(s.foto.storage.exists(archivo))

    def test_eliminar_servicio_borra_su_foto(self):
        s = Servicio.objects.create(nombre='Laptops', precio_base=40)
        self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': imagen()})
        s.refresh_from_db()
        archivo, almacen = s.foto.name, s.foto.storage
        self.client.post(reverse('servicio_eliminar', args=[s.pk]))
        self.assertFalse(almacen.exists(archivo))

    def test_solo_el_admin_sube_fotos(self):
        s = Servicio.objects.create(nombre='Laptops', precio_base=40)
        self.client.force_login(self.cliente)
        self.client.post(reverse('servicio_foto', args=[s.pk]), {'foto': imagen()})
        s.refresh_from_db()
        self.assertFalse(s.foto)

    def test_cambiar_precio_base(self):
        s = Servicio.objects.create(nombre='Laptops', precio_base=40)
        self.client.post(reverse('servicio_precio', args=[s.pk]), {'precio': '45'})
        s.refresh_from_db()
        self.assertEqual(s.precio_base, Decimal('45'))

    def test_eliminar_libre_y_bloqueado_con_ordenes(self):
        libre = Servicio.objects.create(nombre='Libre', precio_base=5)
        usado = Servicio.objects.create(nombre='Usado', precio_base=5)
        Orden.objects.create(cliente=self.cliente, servicio=usado, modelo='HP')
        self.client.post(reverse('servicio_eliminar', args=[libre.pk]))
        r = self.client.post(reverse('servicio_eliminar', args=[usado.pk]))
        self.assertFalse(Servicio.objects.filter(pk=libre.pk).exists())
        self.assertTrue(Servicio.objects.filter(pk=usado.pk).exists())
        self.assertIn('no se puede eliminar', ' '.join(self.mensajes(r)))
