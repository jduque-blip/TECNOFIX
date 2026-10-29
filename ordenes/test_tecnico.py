from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Notificacion
from usuarios.models import Usuario

from .models import NotaOrden, Orden, Servicio
from .views import contexto_panel, resumen_tecnico

CLAVE = 'Taller-2026-ok'


class Base(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser('a@t.com', 'Admin Uno', CLAVE)
        self.cli = Usuario.objects.create_user('c@t.com', 'Ana Cliente', CLAVE, rol='CLIENTE')
        self.tec = Usuario.objects.create_user('t@t.com', 'Tec Uno', CLAVE, rol='TECNICO')
        self.tec2 = Usuario.objects.create_user('t2@t.com', 'Tec Dos', CLAVE, rol='TECNICO')
        self.s1 = Servicio.objects.create(nombre='Computadoras', precio_base=10)
        self.s2 = Servicio.objects.create(nombre='Consolas', precio_base=10)

    def orden(self, tecnico=None, **c):
        datos = {'cliente': self.cli, 'servicio': self.s1, 'modelo': 'HP 15', 'problema': 'No enciende', 'tecnico': tecnico, **c}
        return Orden.objects.create(**datos)

    def entregar(self, o, dias_despues_de_estimada=0, estimada=None):
        hoy = timezone.localdate()
        o.fecha_estimada = estimada or hoy
        o.estado, o.entrega_autorizada = 'ENTREGADO', True
        o.entregada_en = timezone.now() + timedelta(days=dias_despues_de_estimada) if estimada is None else timezone.now()
        o.save()


class EstadoTests(Base):
    def cambiar(self, o, estado):
        return self.client.post(reverse('tecnico_estado', args=[o.pk]), {'estado': estado})

    def test_el_tecnico_avanza_el_estado_y_se_avisa(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.assertRedirects(self.cambiar(o, 'DIAGNOSTICO'), reverse('equipo', args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual(o.estado, 'DIAGNOSTICO')
        self.assertTrue(o.historial.filter(evento__contains='En diagnóstico').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='En diagnóstico').exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='En diagnóstico').exists())

    def test_listo_avisa_al_admin_que_esta_para_entrega(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.cambiar(o, 'LISTO')
        self.assertTrue(Notificacion.objects.filter(usuario=self.admin, texto__contains='lista para entrega').exists())

    def test_no_puede_entregar_ni_inventar_estados(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.cambiar(o, 'ENTREGADO')
        self.cambiar(o, 'XYZ')
        self.cambiar(o, 'RECIBIDO')  # mismo estado
        o.refresh_from_db()
        self.assertEqual((o.estado, o.entregada_en), ('RECIBIDO', None))
        self.assertEqual(o.historial.count(), 0)

    def test_no_toca_ordenes_ajenas_ni_otros_roles(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec2)
        self.assertEqual(self.cambiar(o, 'LISTO').status_code, 404)
        for u in (self.cli, self.admin):
            self.client.force_login(u)
            self.assertEqual(self.cambiar(o, 'LISTO').status_code, 404)
        self.client.logout()
        self.assertEqual(self.cambiar(o, 'LISTO').status_code, 302)
        o.refresh_from_db()
        self.assertEqual(o.estado, 'RECIBIDO')

    def test_orden_entregada_queda_cerrada(self):
        o = self.orden(self.tec)
        self.entregar(o)
        self.client.force_login(self.tec)
        self.cambiar(o, 'LISTO')
        o.refresh_from_db()
        self.assertEqual(o.estado, 'ENTREGADO')

    def test_exige_post(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.assertEqual(self.client.get(reverse('tecnico_estado', args=[o.pk])).status_code, 405)


class NotasTests(Base):
    def nota(self, o, texto='Cambié la fuente de poder', **c):
        return self.client.post(reverse('tecnico_nota', args=[o.pk]), {'texto': texto, **c})

    def test_nota_visible_para_el_cliente(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.nota(o, visible_cliente='on')
        n = NotaOrden.objects.get()
        self.assertEqual((n.autor, n.visible_cliente), (self.tec, True))
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='nota nueva').exists())
        self.client.force_login(self.cli)
        self.assertContains(self.client.get(reverse('equipo', args=[o.pk])), 'Cambié la fuente')

    def test_nota_interna_no_la_ve_el_cliente_pero_si_tecnico_y_admin(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.nota(o)  # sin marcar "visible"
        self.assertFalse(NotaOrden.objects.get().visible_cliente)
        self.assertEqual(Notificacion.objects.filter(usuario=self.cli).count(), 0)
        self.assertContains(self.client.get(reverse('equipo', args=[o.pk])), 'Cambié la fuente')
        self.client.force_login(self.cli)
        self.assertNotContains(self.client.get(reverse('equipo', args=[o.pk])), 'Cambié la fuente')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[o.pk])), 'Cambié la fuente')

    def test_validaciones(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.nota(o, '  ')
        self.nota(o, 'x' * 501)
        self.assertEqual(NotaOrden.objects.count(), 0)

    def test_solo_el_tecnico_dueno(self):
        o = self.orden(self.tec)
        for u in (self.tec2, self.cli, self.admin):
            self.client.force_login(u)
            self.assertEqual(self.nota(o).status_code, 404)
        self.assertEqual(NotaOrden.objects.count(), 0)

    def test_el_texto_se_escapa(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.nota(o, '<script>alert(1)</script>', visible_cliente='on')
        self.assertNotContains(self.client.get(reverse('equipo', args=[o.pk])), '<script>alert(1)</script>')


class PanelTecnicoTests(Base):
    def test_solo_ve_sus_ordenes_ordenadas_por_urgencia(self):
        hoy = timezone.localdate()
        tarde = self.orden(self.tec, modelo='Tarde', fecha_estimada=hoy + timedelta(days=9))
        vencida = self.orden(self.tec, modelo='Vencida', fecha_estimada=hoy - timedelta(days=2))
        sin_fecha = self.orden(self.tec, modelo='SinFecha')
        self.orden(self.tec2, modelo='Ajena')
        ctx = contexto_panel(self.tec)
        self.assertEqual([o.modelo for o in ctx['en_proceso']], ['Vencida', 'Tarde', 'SinFecha'])
        self.assertEqual(ctx['resumen']['atrasadas'], 1)

    def test_busqueda_y_filtro(self):
        self.orden(self.tec, modelo='HP 15')
        self.orden(self.tec, modelo='Xbox One', servicio=self.s2, estado='REPARACION')
        self.client.force_login(self.tec)
        r = self.client.get(reverse('panel'), {'q': 'xbox'})
        self.assertEqual([o.modelo for o in r.context['en_proceso']], ['Xbox One'])
        r = self.client.get(reverse('panel'), {'estado': 'REPARACION'})
        self.assertEqual([o.modelo for o in r.context['en_proceso']], ['Xbox One'])
        r = self.client.get(reverse('panel'), {'q': 'ana'})   # por nombre de cliente
        self.assertEqual(len(r.context['en_proceso']), 2)
        r = self.client.get(reverse('panel'), {'q': 'consolas'})
        self.assertEqual(len(r.context['en_proceso']), 1)
        r = self.client.get(reverse('panel'), {'estado': 'ENTREGADO'})   # no es un filtro válido: se ignora
        self.assertEqual(len(r.context['en_proceso']), 2)

    def test_el_cliente_no_busca_por_nombre_de_cliente_pero_si_por_modelo(self):
        self.orden(self.tec, modelo='HP 15')
        self.client.force_login(self.cli)
        self.assertEqual(len(self.client.get(reverse('panel'), {'q': 'hp'}).context['en_proceso']), 1)
        self.assertNotIn('resumen', self.client.get(reverse('panel')).context)

    def test_cumplimiento_de_entregas(self):
        hoy = timezone.localdate()
        a = self.orden(self.tec, modelo='A')
        b = self.orden(self.tec, modelo='B')
        for o, estimada in ((a, hoy + timedelta(days=1)), (b, hoy - timedelta(days=3))):
            o.fecha_estimada, o.estado, o.entrega_autorizada, o.entregada_en = estimada, 'ENTREGADO', True, timezone.now()
            o.save()
        r = resumen_tecnico(self.tec)
        self.assertEqual((r['entregadas'], r['cumplimiento'], r['abiertas']), (2, 50, 0))

    def test_sin_entregas_no_hay_porcentaje(self):
        self.assertIsNone(resumen_tecnico(self.tec)['cumplimiento'])

    def test_la_pantalla_del_tecnico_muestra_controles_y_el_cliente_no(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        r = self.client.get(reverse('equipo', args=[o.pk]))
        self.assertContains(r, reverse('tecnico_estado', args=[o.pk]))
        self.assertContains(r, reverse('tecnico_nota', args=[o.pk]))
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[o.pk]))
        self.assertNotContains(r, reverse('tecnico_estado', args=[o.pk]))

    def test_etiqueta_de_dias(self):
        hoy = timezone.localdate()
        self.orden(self.tec, modelo='A', fecha_estimada=hoy - timedelta(days=2))
        self.orden(self.tec, modelo='B', fecha_estimada=hoy)
        self.orden(self.tec, modelo='C', fecha_estimada=hoy + timedelta(days=5))
        self.client.force_login(self.tec)
        r = self.client.get(reverse('panel'))
        for texto in ('2 días de atraso', 'vence hoy', 'faltan 5 días'):
            self.assertContains(r, texto)

    def test_entregados_sin_fotos_en_lista_pequena(self):
        o = self.orden(self.tec, modelo='Listo ya')
        self.entregar(o)
        self.client.force_login(self.tec)
        r = self.client.get(reverse('panel'))
        self.assertContains(r, 'Listo ya')
        self.assertEqual(len(r.context['en_proceso']), 0)


import os
import shutil
import tempfile

from django.test import override_settings

from .models import FotoAvance
from .tests import imagen

MEDIA_T = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_T)
class FotosAvanceTests(Base):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_T, ignore_errors=True)

    def subir(self, o, *nombres):
        return self.client.post(reverse('tecnico_fotos', args=[o.pk]), {'fotos': [imagen(n) for n in nombres]})

    def test_sube_varias_y_se_van_sumando(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.subir(o, 'a.png', 'b.png')
        self.subir(o, 'c.png')
        self.assertEqual(o.fotos_avance.count(), 3)
        self.assertEqual(set(o.fotos_avance.values_list('autor', flat=True)), {self.tec.pk})
        self.assertTrue(o.historial.filter(evento__contains='subió 2 fotos').exists())

    def test_el_cliente_las_ve_y_recibe_aviso(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.subir(o, 'a.png')
        self.assertTrue(Notificacion.objects.filter(usuario=self.cli, texto__contains='subió fotos').exists())
        self.client.force_login(self.cli)
        r = self.client.get(reverse('equipo', args=[o.pk]))
        self.assertContains(r, 'Fotos del avance')
        self.assertNotContains(r, reverse('tecnico_foto_quitar', args=[o.pk, o.fotos_avance.first().pk]))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('orden_admin', args=[o.pk])), 'Fotos del avance')

    def test_maximo_ocho_en_total(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.subir(o, *[f'{i}.png' for i in range(6)])
        self.subir(o, 'x.png', 'y.png', 'z.png')
        self.assertEqual(o.fotos_avance.count(), 6)
        self.subir(o, 'x.png', 'y.png')
        self.assertEqual(o.fotos_avance.count(), 8)

    def test_rechaza_archivos_que_no_son_imagen(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.client.post(reverse('tecnico_fotos', args=[o.pk]), {'fotos': [SimpleUploadedFile('a.png', b'no soy imagen')]})
        self.client.post(reverse('tecnico_fotos', args=[o.pk]), {})
        self.assertEqual(o.fotos_avance.count(), 0)

    def test_quitar_borra_el_archivo(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.subir(o, 'a.png')
        f = o.fotos_avance.get()
        ruta = f.imagen.path
        self.client.post(reverse('tecnico_foto_quitar', args=[o.pk, f.pk]))
        self.assertEqual(o.fotos_avance.count(), 0)
        self.assertFalse(os.path.exists(ruta))

    def test_solo_el_tecnico_dueno_y_orden_abierta(self):
        o = self.orden(self.tec)
        for u in (self.tec2, self.cli, self.admin):
            self.client.force_login(u)
            self.assertEqual(self.subir(o, 'a.png').status_code, 404)
        self.client.force_login(self.tec)
        self.subir(o, 'a.png')
        f = o.fotos_avance.get()
        self.client.force_login(self.tec2)
        self.assertEqual(self.client.post(reverse('tecnico_foto_quitar', args=[o.pk, f.pk])).status_code, 404)
        self.entregar(o)
        self.client.force_login(self.tec)
        self.subir(o, 'b.png')
        self.assertEqual(o.fotos_avance.count(), 1)

    def test_la_pantalla_del_tecnico_tiene_el_formulario(self):
        o = self.orden(self.tec)
        self.client.force_login(self.tec)
        self.assertContains(self.client.get(reverse('equipo', args=[o.pk])), reverse('tecnico_fotos', args=[o.pk]))
        self.client.force_login(self.cli)
        self.assertNotContains(self.client.get(reverse('equipo', args=[o.pk])), reverse('tecnico_fotos', args=[o.pk]))
